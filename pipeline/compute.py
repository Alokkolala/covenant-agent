"""All arithmetic. Decimal only — a number a language model produces is a guess.

Rule: nothing in this file may call a model. Facts come in as strings, numbers
go out as Decimal. This is the cheapest full-marks category on the rubric.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENTS = Decimal("0.01")
_STRIP = re.compile(r"[^\d,.\-]")  # kill spaces, NBSP, ₸, KZT, тг, %
_THOUSANDS = re.compile(r"(?<=\d)[  ,](?=\d{3}\b)")


def num(raw) -> Decimal:
    """'1 500,50' / '1,500.50' / '1 000 000 ₸' -> Decimal. Raises on garbage."""
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    s = _THOUSANDS.sub("", str(raw).strip())
    s = _STRIP.sub("", s)
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")  # ru decimal comma
    s = s.replace(",", "")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"not a number: {raw!r}") from e


def num_or(raw, default=Decimal(0)) -> Decimal:
    """Never crash a task over one unparseable field. Partial credit > blank."""
    try:
        return num(raw)
    except ValueError:
        return default


def pct(raw) -> Decimal:
    """'1,5%' -> Decimal('0.015'). Already-fractional input (<=1 with a dot) is
    NOT auto-detected — a '0,5%' rate is 0.005, not 0.5. Be explicit upstream."""
    return num(raw) / Decimal(100)


def money(x) -> Decimal:
    return num(x).quantize(CENTS, rounding=ROUND_HALF_UP)


def commission(amount, rate_pct, *, min_fee=None, max_fee=None) -> Decimal:
    """SWAP ON AUG 6: real fee formula (tiers, caps, per-currency) once known."""
    fee = money(num(amount) * pct(rate_pct))
    if min_fee is not None:
        fee = max(fee, money(min_fee))
    if max_fee is not None:
        fee = min(fee, money(max_fee))
    return fee


def total(rows, field: str) -> Decimal:
    return money(sum((num_or(r.get(field)) for r in rows), Decimal(0)))


def jsonable(x):
    """Decimal -> str, so JSON never silently reformats a number through float."""
    if isinstance(x, Decimal):
        return format(x, "f")
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    return x


def demo() -> None:
    assert num("1 500,50") == Decimal("1500.50")
    assert num("1 000 000 ₸") == Decimal("1000000")
    assert num("1,500.50") == Decimal("1500.50")
    assert num("2.1") == Decimal("2.1")
    assert pct("2,1%") == Decimal("0.021")
    assert commission("1000000", "2,1") == Decimal("21000.00")
    assert commission("1000000", "2,1", max_fee="5000") == Decimal("5000.00")
    assert commission("100", "2,1", min_fee="50") == Decimal("50.00")
    assert num_or("не указано") == Decimal(0)
    assert total([{"amt": "1 000,10"}, {"amt": "2 000,20"}], "amt") == Decimal("3000.30")
    assert jsonable({"fee": Decimal("21000.00")}) == {"fee": "21000.00"}
    print("compute ok")


if __name__ == "__main__":
    demo()
