import unittest

from app.modules.market_data.nifty200 import Nifty200UniverseError, parse_nifty200_csv


class Nifty200CsvTests(unittest.TestCase):
    def test_parses_symbol_column_and_deduplicates(self):
        rows = ["Company Name,Industry,Symbol,Series,ISIN Code"]
        rows.extend(f"Company {i},Industry,SYM{i},EQ,IN{i}" for i in range(1, 201))
        rows.append("Duplicate,Industry,SYM1,EQ,IN1")
        symbols = parse_nifty200_csv("\n".join(rows))
        self.assertEqual(len(symbols), 200)
        self.assertEqual(symbols[0], "SYM1")
        self.assertEqual(symbols[-1], "SYM200")

    def test_rejects_malformed_or_incomplete_universe(self):
        with self.assertRaises(Nifty200UniverseError):
            parse_nifty200_csv("Company,Industry\nA,B")


if __name__ == "__main__":
    unittest.main()
