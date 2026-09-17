from __future__ import annotations


def money(value: float, precision: int) -> float:
    """Normalize a monetary value using the configured precision."""
    return round(float(value), precision)


def price(value: float, precision: int) -> float:
    """Normalize a market price using the configured precision."""
    return round(float(value), precision)
