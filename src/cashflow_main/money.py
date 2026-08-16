"""金额和显示单位转换。"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

UNIT_FACTORS = {
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "百万元": Decimal("1000000"),
}

_UNIT_DECIMAL_PLACES = {
    "元": 2,
    "千元": 5,
    "万元": 6,
    "百万元": 8,
}


def to_minor_units(value: object, display_unit: str) -> int:
    """把显示金额转换为最小货币单位整数。"""
    if display_unit not in UNIT_FACTORS:
        raise ValueError(f"不支持的金额单位：{display_unit}")
    text = str(value).replace(",", "").replace("，", "").replace("－", "-").strip()
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"无法识别金额：{value}") from exc
    exponent = amount.as_tuple().exponent
    if (
        isinstance(value, str)
        and isinstance(exponent, int)
        and exponent < 0
        and -exponent > _UNIT_DECIMAL_PLACES[display_unit]
    ):
        raise ValueError(f"金额小数位超过{display_unit}允许精度：{value}")
    minor = amount * UNIT_FACTORS[display_unit] * Decimal("100")
    return int(minor.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_minor_units(amount_minor: int, display_unit: str) -> Decimal:
    """把最小货币单位整数转换为指定显示单位。"""
    if display_unit not in UNIT_FACTORS:
        raise ValueError(f"不支持的金额单位：{display_unit}")
    return (
        Decimal(amount_minor)
        / Decimal("100")
        / UNIT_FACTORS[display_unit]
    ).quantize(Decimal("0.01"))
