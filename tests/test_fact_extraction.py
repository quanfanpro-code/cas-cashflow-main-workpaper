from cashflow_main.adjustment_bridge import (
    AdjustmentBridgeResult,
    AdjustmentBridgeRow,
    AdjustmentRecord,
)
from cashflow_main.contracts import (
    AccountBalance,
    JournalPair,
    NormalizedInputBundle,
    StatementLine,
    EnterpriseType,
)
from cashflow_main.fact_extraction import extract_facts


def test_fact_ledger_keeps_sources_and_adjustment_tags():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(StatementLine("应收账款", 800, 1000),),
        audited_income_statement=(StatementLine("营业收入", 5000),),
        trial_balance=(AccountBalance("1122", "应收账款", 1000, 300, 500, 800),),
        journal_pairs=(JournalPair("银行存款", "应收账款", 500),),
        prior_cashflow=(),
    )
    adjustment = AdjustmentRecord("ADJ:001", "应收账款", 20, "audit", "noncash")
    bridge = AdjustmentBridgeResult(
        rows=(AdjustmentBridgeRow("应收账款", 780, 0, 20, 800, 20, 0, (adjustment,), ()),),
        orphan_adjustments=(),
        is_amount_reconciled=True,
    )
    ledger = extract_facts(bundle, bridge, EnterpriseType.GENERAL, {})
    assert ledger.by_id["TB:1122:closing_change"].source_ids == ("trial_balance:1122",)
    assert ledger.by_id["TB:1122:closing_change"].occupancy_key == "TB:1122:closing_change"
    assert "noncash" in ledger.by_id["ADJ:001"].tags
    assert ledger.by_id["JP:1"].amount_minor == 500


def test_fact_ledger_uses_derived_tags_and_keeps_conflict_evidence():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(),
        journal_pairs=(JournalPair(
            "银行存款",
            "短期借款",
            500,
            {"现金流标签": "investment_acquisition_cash"},
        ),),
        prior_cashflow=(),
    )
    bridge = AdjustmentBridgeResult((), (), True)

    fact = extract_facts(bundle, bridge, EnterpriseType.GENERAL, {}).by_id["JP:1"]

    assert "short_term_borrowing_cash_received" in fact.tags
    assert "investment_acquisition_cash" not in fact.tags
    assert fact.metadata["tag_conflicts"]


def test_one_journal_pair_can_supply_distinct_workpaper_adjustments_without_shared_occupancy():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(),
        journal_pairs=(JournalPair(
            "在建工程",
            "应付职工薪酬",
            500,
            {"摘要": "计提工程人员工资"},
        ),),
        prior_cashflow=(),
    )

    facts = extract_facts(
        bundle,
        AdjustmentBridgeResult((), (), True),
        EnterpriseType.GENERAL,
        {"employee_benefits_payable_operating": ["应付职工薪酬"]},
    )
    capex_accrual = next(
        fact for fact in facts.values()
        if "capex_employee_accrual" in fact.tags
    )
    capex_adjustment = next(
        fact for fact in facts.values()
        if "operating_employee_capex_accrual_adjustment" in fact.tags
    )

    assert capex_accrual.amount_minor == 500
    assert capex_adjustment.amount_minor == 500
    assert capex_accrual.occupancy_key != capex_adjustment.occupancy_key


def test_configured_capex_prepayment_is_investing_cash_without_false_operating_adjustment():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(),
        journal_pairs=(JournalPair(
            "预付机器款",
            "银行存款",
            500,
            {"摘要": "支付机器采购定金"},
        ),),
        prior_cashflow=(),
    )

    facts = extract_facts(
        bundle,
        AdjustmentBridgeResult((), (), True),
        EnterpriseType.GENERAL,
        {
            "cash_and_equivalents": ["银行存款"],
            "capex_prepayments": ["预付机器款"],
            "trade_prepayments": ["预付账款"],
        },
    )

    assert any("long_lived_asset_cash_addition" in fact.tags for fact in facts.values())
    assert all("capex_prepayment_change" not in fact.tags for fact in facts.values())


def test_capex_payment_keeps_conflicting_supplied_cashflow_tag_visible():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(),
        journal_pairs=(JournalPair(
            "应付工程款",
            "银行存款",
            500,
            {"摘要": "支付工程款", "现金流标签": "other_operating_cash_paid"},
        ),),
        prior_cashflow=(),
    )

    facts = extract_facts(
        bundle,
        AdjustmentBridgeResult((), (), True),
        EnterpriseType.GENERAL,
        {
            "cash_and_equivalents": ["银行存款"],
            "capex_payables": ["应付工程款"],
        },
    )
    payment = next(fact for fact in facts.values() if "capex_payable_cash_paid" in fact.tags)

    assert payment.metadata["tag_conflicts"] == ("other_operating_cash_paid",)
