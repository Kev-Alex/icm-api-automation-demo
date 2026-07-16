"""
Pruebas unitarias del cliente. No requieren servidor ni dependencias externas:
se ejecutan con la libreria estandar.

    python -m unittest discover -s tests -v
    # o, si tienes pytest instalado:
    pytest -q
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from icm_client import IcmApiClient
from mock_icm_server.server import decode_filter, parse_values_query


class TestFilterEncoding(unittest.TestCase):
    def test_single_value(self):
        route = IcmApiClient.generate_filter_route({"YearTxt": ["2024"]})
        self.assertEqual(route, "&filter=YearTxt%3D2024")

    def test_multiple_values(self):
        route = IcmApiClient.generate_filter_route({"Period": ["202401", "202402"]})
        self.assertEqual(route, "&filter=Period%3D202401%5C,202402")

    def test_multiple_fields(self):
        route = IcmApiClient.generate_filter_route(
            {"YearTxt": ["2024"], "Period": ["202401", "202402"]}
        )
        self.assertEqual(route, "&filter=YearTxt%3D2024%3BPeriod%3D202401%5C,202402")

    def test_empty_filter(self):
        self.assertEqual(IcmApiClient.generate_filter_route({}), "")

    def test_roundtrip_with_decoder(self):
        # Lo que codifica el cliente debe poder decodificarlo el servidor.
        original = {"YearTxt": ["2024"], "Period": ["202401", "202402", "202403"]}
        route = IcmApiClient.generate_filter_route(original)
        raw = route.replace("&filter=", "")
        self.assertEqual(decode_filter(raw), original)

    def test_range_filter_roundtrip(self):
        f = IcmApiClient.build_range_filter("Date", "2022-01-01", "2022-01-31")
        route = IcmApiClient.generate_filter_route(f)
        decoded = decode_filter(route.replace("&filter=", ""))
        self.assertEqual(decoded, {"Date": ["[2022-01-01", "2022-01-31]"]})


class TestSqlValues(unittest.TestCase):
    def test_none_when_empty(self):
        self.assertIsNone(IcmApiClient.to_sql_values_query([]))

    def test_preserves_leading_zeros_and_escapes(self):
        rows = [{"id": "007", "name": "O'Brien"}, {"id": "042", "name": "Ann"}]
        query = IcmApiClient.to_sql_values_query(rows)
        self.assertIn("'007'", query)          # cero a la izquierda preservado
        self.assertIn("O''Brien", query)        # comilla simple escapada
        self.assertIn('AS t("id", "name")', query)

    def test_null_for_missing(self):
        rows = [{"a": "1", "b": None}]
        query = IcmApiClient.to_sql_values_query(rows)
        self.assertIn("NULL", query)


class TestValuesParser(unittest.TestCase):
    def test_client_to_server_roundtrip(self):
        rows = [{"PayeeID": "008", "Region": "Norte"}, {"PayeeID": "015", "Region": "Sur"}]
        query = IcmApiClient.to_sql_values_query(rows)
        columns, parsed = parse_values_query(query)
        self.assertEqual(columns, ["PayeeID", "Region"])
        self.assertEqual(parsed, [["008", "Norte"], ["015", "Sur"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
