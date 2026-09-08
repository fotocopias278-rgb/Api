"""Conversión de importes."""

from decimal import Decimal

import pytest

from app.core.money import currency_exponent, format_amount, to_decimal, to_minor


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        ("15.00", "EUR", 1500),
        ("0.01", "EUR", 1),
        ("15.005", "EUR", 1501),  # redondeo half-up
        ("1500", "JPY", 1500),  # divisa sin decimales
        ("1.234", "KWD", 1234),  # divisa de tres decimales
    ],
)
def test_to_minor(amount, currency, expected):
    assert to_minor(Decimal(amount), currency) == expected


def test_to_minor_rechaza_texto_no_numerico():
    with pytest.raises(ValueError, match="no numérico"):
        to_minor("mil euros", "EUR")


@pytest.mark.parametrize(
    ("currency", "exponent"), [("EUR", 2), ("USD", 2), ("JPY", 0), ("TND", 3), ("xyz", 2)]
)
def test_exponentes(currency, exponent):
    assert currency_exponent(currency) == exponent


def test_ida_y_vuelta():
    assert to_decimal(to_minor("99.99", "EUR"), "EUR") == Decimal("99.99")


def test_formato_legible():
    assert format_amount(1500, "EUR") == "15.00"
    assert format_amount(1500, "JPY") == "1500"
