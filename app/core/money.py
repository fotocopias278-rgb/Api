"""Conversión entre importes decimales y unidades mínimas (ISO 4217)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Divisas cuyo exponente decimal no es 2. El resto usa 2 por defecto.
_EXPONENTS: dict[str, int] = {
    "BIF": 0, "CLP": 0, "DJF": 0, "GNF": 0, "ISK": 0, "JPY": 0, "KMF": 0,
    "KRW": 0, "PYG": 0, "RWF": 0, "UGX": 0, "VND": 0, "VUV": 0, "XAF": 0,
    "XOF": 0, "XPF": 0,
    "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "LYD": 3, "OMR": 3, "TND": 3,
}


def currency_exponent(currency: str) -> int:
    """Número de decimales de la divisa."""
    return _EXPONENTS.get(currency.upper(), 2)


def to_minor(amount: Decimal | str, currency: str) -> int:
    """Convierte un importe decimal a unidades mínimas.

    Se usa `Decimal` en todo el camino: `float` introduce errores de redondeo
    inaceptables en importes monetarios.
    """
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Importe no numérico: {amount!r}") from exc

    factor = Decimal(10) ** currency_exponent(currency)
    return int((value * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_decimal(amount_minor: int, currency: str) -> Decimal:
    """Convierte unidades mínimas al importe decimal de la divisa."""
    factor = Decimal(10) ** currency_exponent(currency)
    return (Decimal(amount_minor) / factor).quantize(
        Decimal(1).scaleb(-currency_exponent(currency))
    )


def format_amount(amount_minor: int, currency: str) -> str:
    """Representación legible del importe, p. ej. `1500 EUR -> '15.00'`."""
    return str(to_decimal(amount_minor, currency))
