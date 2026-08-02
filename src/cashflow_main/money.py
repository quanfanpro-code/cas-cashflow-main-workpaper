"""金额和显示单位转换。"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

UNIT_FACTORS = {
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "百万元": Decimal("1000000"),
}


def to_minor_units(value: object, display_unit: str) -> int:
    """把显示金额转换为最小货币单位整数。"""
    if display_unit not in UNIT_FACTORS:
        raise ValueError(f"不支持的金额单位：{display_unit}")
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"无法识别金额：{value}") from exc
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
