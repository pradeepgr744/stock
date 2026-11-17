"""Conservative, explainable news-risk assessment for NSE intraday trading.

This is a risk filter, not a price predictor or an LLM-generated trading signal.
It only evaluates articles supplied by trusted feed adapters. It never submits
orders and never treats an unverified headline as a directional recommendation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
from enum import Enum
from hashlib import sha256
from math import isfinite
from typing import Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")


class NewsState(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class NewsArticle:
    title: str
    source: str
    url: str
    published_at: datetime
    symbols: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class ArticleAssessment:
    article_id: str
    title: str
    source: str
    symbols: tuple[str, ...]
    age_minutes: float
    sentiment_score: float
    sentiment_label: str
    event_severity: int
    source_trust: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class NewsAssessment:
    state: NewsState
    halt_new_entries: bool
    halt_all_symbols: bool
    blocked_symbols: tuple[str, ...]
    sentiment_score: float
    articles: tuple[ArticleAssessment, ...]
    reasons: tuple[str, ...]


# These are intentionally coarse risk terms. A feed adapter must preserve the
# original headline and URL so the operator can verify the underlying filing.
_MARKET_WIDE_TERMS = (
    "repo rate decision", "monetary policy decision", "rbi raises repo rate",
    "rbi cuts repo rate", "rbi policy decision", "market-wide circuit",
    "exchange outage", "nse trading halt", "bse trading halt",
    "market trading halt", "cyber attack on exchange", "cyberattack on exchange",
    "war declared", "military strike", "terror attack", "capital controls",
)
_ISSUER_BLOCK_TERMS = (
    "trading suspension", "default", "insolvency", "bank failure",
    "sebi ban", "company banned", "promoter arrested",
)
_MEDIUM_IMPACT_TERMS = (
    "investigation", "regulatory action", "search operation", "raid",
    "resignation", "ceo steps down", "guidance cut", "profit warning",
    "rating downgrade", "credit downgrade", "fraud", "fire", "explosion",
    "plant shutdown", "production halt", "data breach", "block deal",
    "bulk deal", "earnings", "results", "acquisition", "merger",
    "open offer", "pledge", "stake sale",
)
_POSITIVE_TERMS = (
    "beats estimates", "profit rises", "record profit", "upgrade",
    "order win", "contract win", "dividend", "buyback", "debt reduced",
    "guidance raised", "margin expands",
)
_NEGATIVE_TERMS = (
    "misses estimates", "loss widens", "profit falls", "downgrade",
    "order cancelled", "guidance cut", "margin contracts", "default",
    "fraud", "resigns", "plant shutdown", "regulatory action",
)
_TRUSTED_DOMAINS = {
    "nseindia.com", "bseindia.com", "sebi.gov.in", "rbi.org.in",
    "pib.gov.in", "mca.gov.in",
}


def _local_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("published_at and as_of must be timezone-aware")
    return value.astimezone(IST)


def _domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _trust(url: str) -> str:
    host = _domain(url)
    if not host:
        return "UNVERIFIED"
    if any(host == domain or host.endswith("." + domain) for domain in _TRUSTED_DOMAINS):
        return "OFFICIAL"
    if host.endswith(".com") or host.endswith(".in") or host.endswith(".co.in"):
        return "THIRD_PARTY"
    return "UNVERIFIED"


def _contains(text: str, terms: Iterable[str]) -> list[str]:
    lowered = text.casefold()
    return [term for term in terms if term in lowered]


def _sentiment(text: str) -> tuple[float, str]:
    positive = _contains(text, _POSITIVE_TERMS)
    negative = _contains(text, _NEGATIVE_TERMS)
    total = len(positive) + len(negative)
    if not total:
        return 0.0, "NEUTRAL_OR_UNCLEAR"
    score = (len(positive) - len(negative)) / total
    label = "POSITIVE" if score > 0.2 else "NEGATIVE" if score < -0.2 else "MIXED"
    return round(score, 3), label


def assess_news(
    articles: Iterable[NewsArticle],
    *,
    as_of: datetime,
    relevant_symbols: Iterable[str] = (),
    max_article_age_minutes: int = 90,
) -> NewsAssessment:
    """Assess fresh news; official high-impact events can block new entries.

    A blocked state means "pause new entries and request operator review".
    It does not automatically liquidate open positions: an execution/risk
    controller must separately manage existing positions.
    """
    now = _local_time(as_of)
    if max_article_age_minutes < 1:
        raise ValueError("max_article_age_minutes must be positive")
    relevant = {symbol.strip().upper() for symbol in relevant_symbols if symbol.strip()}
    seen: set[str] = set()
    assessments: list[ArticleAssessment] = []
    global_reasons: list[str] = []
    weighted_sentiment = 0.0
    sentiment_weight = 0.0
    blocked = False
    blocked_symbols: set[str] = set()
    caution = False

    for article in articles:
        published = _local_time(article.published_at)
        age = (now - published).total_seconds() / 60.0
        if age < -5:
            caution = True
            global_reasons.append("Future-dated article rejected from scoring")
            continue
        if age > max_article_age_minutes:
            continue
        title = article.title.strip()
        url = article.url.strip()
        source = article.source.strip()
        if not title or not url or not source:
            continue
        digest = sha256(url.casefold().encode("utf-8")).hexdigest()[:16]
        if digest in seen:
            continue
        seen.add(digest)

        body = f"{title} {article.summary}".casefold()
        trust = _trust(url)
        market_terms = _contains(body, _MARKET_WIDE_TERMS)
        issuer_block_terms = _contains(body, _ISSUER_BLOCK_TERMS)
        medium_terms = _contains(body, _MEDIUM_IMPACT_TERMS)
        high_terms = market_terms + issuer_block_terms
        score, label = _sentiment(body)
        symbols = tuple(sorted({s.strip().upper() for s in article.symbols if s.strip()}))
        matched = not relevant or not symbols or bool(relevant.intersection(symbols))
        severity = 3 if high_terms else 2 if medium_terms else 0
        reasons: list[str] = []
        if high_terms:
            reasons.append("High-impact event language: " + ", ".join(high_terms))
        if medium_terms:
            reasons.append("Potentially material event: " + ", ".join(medium_terms))
        if trust == "OFFICIAL":
            reasons.append("Source domain is on the official-source allowlist")
        elif trust == "THIRD_PARTY":
            reasons.append("Third-party source; verify against an exchange/company filing")
        else:
            reasons.append("Source domain is unverified; do not trade on this headline alone")
        if not matched:
            reasons.append("Article does not match the requested symbol set")

        # Only a fresh official source can create an automatic broad market
        # entry pause. Third-party claims can raise caution, never hard-block.
        if matched and trust == "OFFICIAL" and age <= 45 and market_terms:
            blocked = True
            global_reasons.append(f"Official market-wide event requires review: {title}")
        elif matched and trust == "OFFICIAL" and age <= 45 and issuer_block_terms and symbols:
            blocked_symbols.update(symbols if not relevant else set(symbols).intersection(relevant))
            caution = True
        elif matched and (severity > 0 or trust != "OFFICIAL"):
            caution = True

        if matched and trust == "OFFICIAL" and age <= max_article_age_minutes:
            weight = max(0.1, 1.0 - age / max_article_age_minutes)
            weighted_sentiment += score * weight
            sentiment_weight += weight

        assessments.append(ArticleAssessment(
            article_id=digest,
            title=title,
            source=source,
            symbols=symbols,
            age_minutes=round(age, 1),
            sentiment_score=score,
            sentiment_label=label,
            event_severity=severity,
            source_trust=trust,
            reasons=tuple(reasons),
        ))

    aggregate = weighted_sentiment / sentiment_weight if sentiment_weight else 0.0
    state = NewsState.BLOCKED if blocked or blocked_symbols else NewsState.CAUTION if caution else NewsState.NORMAL
    if not assessments:
        global_reasons.append("No fresh, usable news articles were supplied")
    return NewsAssessment(
        state=state,
        halt_new_entries=blocked or bool(blocked_symbols),
        halt_all_symbols=blocked,
        blocked_symbols=tuple(sorted(blocked_symbols)),
        sentiment_score=round(aggregate, 3),
        articles=tuple(assessments),
        reasons=tuple(global_reasons),
    )



def fetch_rss_articles(
    feed_urls: Iterable[str],
    *,
    timeout_seconds: float = 5.0,
    max_items_per_feed: int = 100,
) -> tuple[list[NewsArticle], list[str]]:
    """Fetch configured RSS/Atom feeds; return partial results plus safe errors.

    Feed URLs must be configured by the operator from the publisher's official
    site. This adapter does not discover feeds or scrape social networks.
    """
    articles: list[NewsArticle] = []
    errors: list[str] = []
    for feed_url in feed_urls:
        url = feed_url.strip()
        if not url:
            continue
        # Only HTTPS feeds on the official exchange/regulator allowlist are
        # fetched. This avoids turning a configurable feed into an SSRF proxy.
        if not url.lower().startswith("https://") or _trust(url) != "OFFICIAL":
            errors.append(f"Rejected non-HTTPS or non-official feed: {_domain(url) or 'unknown domain'}")
            continue
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": "IntradayResearchBot/1.0"},
                timeout=timeout_seconds,
                follow_redirects=True,
            )
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                errors.append(f"Feed response exceeded 2 MB: {_domain(url)}")
                continue
            root = ET.fromstring(response.content)
        except (httpx.HTTPError, ET.ParseError, ValueError):
            errors.append(f"Could not retrieve or parse configured feed: {_domain(url) or 'unknown domain'}")
            continue

        # RSS 2.0 uses item; Atom uses entry. Namespace-insensitive child lookup.
        nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] in {"item", "entry"}]
        for node in nodes[:max_items_per_feed]:
            fields: dict[str, str] = {}
            categories: list[str] = []
            for child in list(node):
                name = child.tag.rsplit("}", 1)[-1].lower()
                value = (child.text or "").strip()
                if name == "link" and not value:
                    value = child.attrib.get("href", "").strip()
                if name == "category":
                    term = child.attrib.get("term", value).strip()
                    if term:
                        categories.append(term)
                elif name in {"title", "link", "pubdate", "published", "updated", "description", "summary"}:
                    fields.setdefault(name, value)

            date_value = fields.get("pubdate") or fields.get("published") or fields.get("updated")
            if not fields.get("title") or not fields.get("link") or not date_value:
                continue
            try:
                try:
                    published_at = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
                except ValueError:
                    published_at = parsedate_to_datetime(date_value)
                if published_at.tzinfo is None:
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            articles.append(NewsArticle(
                title=fields["title"],
                source=_domain(url) or "configured-feed",
                url=fields["link"],
                published_at=published_at,
                symbols=tuple(categories),
                summary=fields.get("description", "") or fields.get("summary", ""),
            ))
    return articles, errors
