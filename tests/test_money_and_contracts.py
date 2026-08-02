from decimal import Decimal

import pytest

from cashflow_main.contracts import EnterpriseType, RunState, RunStatus
from cashflow_main.money import from_minor_units, to_minor_units


def test_amounts_use_integer_minor_units():
    assert to_minor_units("1.23", "元") == 123
    assert to_minor_units(Decimal("2.5"), "万元") == 2_500_000
    assert from_minor_units(2_500_000, "万元") == Decimal("2.50")


def test_invalid_amount_has_readable_chinese_error():
    with pytest.raises(ValueError, match="无法识别金额"):
        to_minor_units("一百元", "元")


def test_status_transition_is_explicit():
    state = RunState(run_id="r1")
    state.advance(RunStatus.INPUT_SNAPSHOTTED)
    with pytest.raises(ValueError, match="非法状态迁移"):
        state.advance(RunStatus.EXPORTED)


def test_industry_values_cover_general_and_financial_entities():
    assert {item.value for item in EnterpriseType} == {
        "general",
        "bank",
        "securities",
        "insurance",
        "other_financial",
    }
