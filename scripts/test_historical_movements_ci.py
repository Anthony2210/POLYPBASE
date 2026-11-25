"""Offline tests for historical movement catalog normalization."""

import unittest

import validate_historical_movements_ci as validation


class HistoricalMovementsCatalogTests(unittest.TestCase):
    def test_postgresql_type_aliases_preserve_column_shape(self):
        for left, right in (("varchar(64)", "character varying(64)"),
                            ("int8", "bigint"), ("bool", "boolean")):
            self.assertEqual(validation.normalize_pg_type(left), validation.normalize_pg_type(right))
        for left, right in (("varchar(64)", "varchar(63)"), ("numeric(10,2)", "numeric(10,3)"),
                            ("bigint", "integer"), ("integer[]", "integer"),
                            ('"varchar"', "varchar")):
            self.assertNotEqual(validation.normalize_pg_type(left), validation.normalize_pg_type(right))
