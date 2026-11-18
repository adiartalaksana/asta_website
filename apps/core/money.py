from decimal import Decimal, InvalidOperation

from .constants import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS, MONEY_QUANTUM

MONEY_LIMIT = Decimal(10) ** (MONEY_MAX_DIGITS - MONEY_DECIMAL_PLACES)


def parse_money(value, *, lenient: bool = False) -> Decimal:
    """Parse a monetary value into a finite Decimal that fits Decimal(18, 4).

    Raises InvalidOperation for anything that is not a usable amount (empty, NaN,
    Infinity, too many digits). ``lenient`` accepts spreadsheet/bank formats such as
    ``Rp 1.234.567,89`` or ``1,234,567.89``; form input should stay strict.
    """
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int | float) and not isinstance(value, bool):
        amount = Decimal(str(value))
    else:
        text = str(value or "").strip()
        if lenient:
            text = _normalise_number_text(text)
        if not text:
            raise InvalidOperation("Empty amount.")
        amount = Decimal(text)
    if not amount.is_finite():
        raise InvalidOperation("Amount must be finite.")
    amount = amount.quantize(MONEY_QUANTUM)
    if abs(amount) >= MONEY_LIMIT:
        raise InvalidOperation("Amount is too large.")
    return amount


def _normalise_number_text(text: str) -> str:
    text = text.replace("Rp", "").replace("IDR", "").replace(" ", "").replace(" ", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")  # 1.234.567,89
        else:
            text = text.replace(",", "")  # 1,234,567.89
    elif "," in text:
        whole, _, fraction = text.rpartition(",")
        if text.count(",") > 1 or len(fraction) == 3:
            text = text.replace(",", "")  # 1,234,567
        else:
            text = f"{whole}.{fraction}"  # 1234,5
    elif text.count(".") > 1 or (text.count(".") == 1 and len(text.rpartition(".")[2]) == 3):
        text = text.replace(".", "")  # 1.234.567 / 15.000 (Indonesian thousands; IDR has no sub-units)
    return f"-{text}" if negative and text else text
