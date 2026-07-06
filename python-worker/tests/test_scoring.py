"""Unit tests for the worker's pure scoring/normalization logic."""
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from worker import DocumentProcessor as D  # noqa: E402


def test_parse_amount_handles_currency_and_commas():
    assert D._parse_amount("$42.10") == Decimal("42.10")
    assert D._parse_amount("1,234.56") == Decimal("1234.56")
    assert D._parse_amount("27.35") == Decimal("27.35")


def test_parse_amount_rejects_junk():
    assert D._parse_amount(None) is None
    assert D._parse_amount("abc") is None
    assert D._parse_amount("") is None


def test_parse_date_accepts_common_formats():
    assert D._parse_date("2026-01-15") == date(2026, 1, 15)
    assert D._parse_date("01/15/2026") == date(2026, 1, 15)


def test_parse_date_rejects_junk():
    assert D._parse_date("not a date") is None
    assert D._parse_date(None) is None


def test_clean_value_rejects_echoed_placeholders():
    assert D._clean_value("the store or company name exactly as printed") is None
    assert D._clean_value("<store name>") is None
    assert D._clean_value("item name") is None
    assert D._clean_value("") is None


def test_clean_value_keeps_real_vendors():
    assert D._clean_value("DINEFINE RESTAURANT") == "DINEFINE RESTAURANT"
    assert D._clean_value("Green Supermarket") == "Green Supermarket"
    assert D._clean_value("ABC Company") == "ABC Company"
