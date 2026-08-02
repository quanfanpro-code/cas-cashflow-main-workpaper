from cashflow_main.completeness_check import validate_completeness
from cashflow_main.contracts import EnterpriseType
from cashflow_main.fact_extraction import Fact, FactLedger
from cashflow_main.item_calculators import (
    CalculationResult,
    CashflowItemResult,
    ItemComponentResult,
)
from cashflow_main.rule_loader import RuleComponent, RuleItem, RulePack


def result(items, allocated=None):
    rows = tuple(CashflowItemResult(i, i, s, n, a, ()) for n, (i, s, a) in enumerate(items))
    return CalculationResult(rows, allocated or {})


def test_clean_allocation_subtotals_and_cash_change():
    facts = FactLedger.index((Fact("F1", 100, ("control",), ("src",), "K1"),))
    calculation = result([
        ("CFO-IN", "operating", 100), ("CFO-OUT", "operating", 20), ("CFO-NET", "operating", 80),
        ("CFI-NET", "investing", 10), ("CFF-NET", "financing", -5), ("CF-NET", "cash", 85),
    ], {"K1": ("C1",)})
    report = validate_completeness(facts, calculation, RulePack("1", EnterpriseType.GENERAL, (), {}, ()), 1000, 1085)
    assert report.is_clean
    assert report.cash_change_difference_minor == 0


def test_unallocated_control_fact_is_blocking_not_human_review():
    facts = FactLedger.index((Fact("F-unallocated", 500, ("control",), ("src",), "K"),))
    calculation = result([("CF-NET", "cash", 0)])
    report = validate_completeness(facts, calculation, RulePack("1", EnterpriseType.GENERAL, (), {}, ()), 100, 100)
    assert report.is_blocking
    assert report.unallocated[0].amount_minor == 500
    assert report.human_review_cases == ()


def test_rule_referenced_semantic_fact_is_a_control_fact_without_manual_tag():
    facts = FactLedger.index((Fact("F-semantic", 500, ("business_cash",), ("src",), "K"),))
    component = RuleComponent(
        "X-C1", "fact_amount", 1, "fact_ledger",
        {"tags_any": ["business_cash"]}, "exclusive",
    )
    pack = RulePack(
        "1", EnterpriseType.GENERAL, (), {},
        (RuleItem("X", "业务现金", "operating", 1, "V", (component,)),),
    )

    report = validate_completeness(
        facts, result([("CF-NET", "cash", 0)]), pack, 100, 100
    )

    assert report.is_blocking
    assert report.unallocated[0].fact_id == "F-semantic"


def test_duplicate_allocation_and_bad_cash_change_block():
    facts = FactLedger.index((Fact("F1", 100, ("control",), ("src",), "K"),))
    calculation = result([("CF-NET", "cash", 80)], {"K": ("C1", "C2")})
    report = validate_completeness(facts, calculation, RulePack("1", EnterpriseType.GENERAL, (), {}, ()), 100, 200)
    assert report.duplicate_allocations
    assert report.cash_change_difference_minor == -20
    assert report.is_blocking


def test_unclassified_cash_fact_is_listed_with_its_amount():
    facts = FactLedger.index((
        Fact("JP:8", 500, ("journal_pair", "unclassified_cash"), ("journal_pair:8",), "JP:8"),
    ))

    report = validate_completeness(
        facts,
        result([("CF-NET", "cash", 0)]),
        RulePack("1", EnterpriseType.GENERAL, (), {}, ()),
        100,
        100,
    )

    assert report.is_blocking
    assert report.unallocated[0].fact_id == "JP:8"
    assert report.unallocated[0].amount_minor == 500
