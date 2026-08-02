import pytest

from cashflow_main.contracts import EnterpriseType, JournalPair
from cashflow_main.semantic_facts import classify_pair


@pytest.mark.parametrize(
    ("debit", "credit", "expected_tag"),
    [
        ("银行存款", "短期借款", "short_term_borrowing_cash_received"),
        ("长期借款", "银行存款", "debt_principal_cash_repaid"),
        ("银行存款", "实收资本", "equity_investment_cash_received"),
        ("长期股权投资", "银行存款", "investment_acquisition_cash"),
        ("银行存款", "长期股权投资", "investment_principal_recovered"),
        ("固定资产", "银行存款", "long_lived_asset_cash_addition"),
        ("固定资产", "应付账款", "noncash_long_lived_asset_addition"),
        ("银行存款", "应收股利", "cash_dividend_received"),
        ("应付利息", "银行存款", "interest_cash_paid"),
        ("利润分配—应付股利", "银行存款", "dividend_cash_paid"),
        ("应交税费—应交增值税（进项税额）", "应付账款", "purchase_input_tax"),
        ("应收账款", "应交税费—应交增值税（销项税额）", "sales_output_tax"),
        ("坏账准备", "应收账款", "receivable_write_off"),
        ("信用减值损失", "坏账准备", "bad_debt_accrual"),
        ("处置子公司费用", "银行存款", "business_disposal_cost_cash_paid"),
        ("股票发行费用", "银行存款", "equity_issue_cost_cash_paid"),
        ("债券发行费用", "银行存款", "bond_issue_cost_cash_paid"),
    ],
)
def test_paired_accounts_generate_deterministic_supplementary_fact(
    debit, credit, expected_tag
):
    result = classify_pair(JournalPair(debit, credit, 10_000), EnterpriseType.GENERAL)

    assert expected_tag in result.tags
    assert result.evidence
    assert not result.conflicts


@pytest.mark.parametrize(
    ("enterprise_type", "debit", "credit", "expected_tag"),
    [
        (EnterpriseType.SECURITIES, "银行存款", "融出资金", "margin_financing_cash_received"),
        (EnterpriseType.SECURITIES, "融出资金", "银行存款", "margin_financing_cash_paid"),
        (EnterpriseType.INSURANCE, "银行存款", "保户储金及投资款", "policyholder_deposit_cash_received"),
        (EnterpriseType.INSURANCE, "保户储金及投资款", "银行存款", "policyholder_deposit_cash_paid"),
        (EnterpriseType.INSURANCE, "应付保单红利", "银行存款", "policy_dividend_cash_paid"),
        (EnterpriseType.INSURANCE, "银行存款", "保户质押贷款", "policy_pledge_loan_cash_received"),
        (EnterpriseType.INSURANCE, "银行存款", "分出再保险合同资产", "outward_reinsurance_cash_received"),
        (EnterpriseType.INSURANCE, "分入再保险合同负债", "银行存款", "inward_reinsurance_cash_paid"),
        (EnterpriseType.OTHER_FINANCIAL, "银行存款", "营业收入", "financial_sales_service_cash_received"),
    ],
)
def test_financial_paired_accounts_generate_specialized_facts(
    enterprise_type, debit, credit, expected_tag
):
    result = classify_pair(JournalPair(debit, credit, 10_000), enterprise_type)

    assert expected_tag in result.tags
    assert result.evidence
    assert not result.conflicts


def test_supplied_cashflow_label_cannot_override_conflicting_account_evidence():
    pair = JournalPair(
        "银行存款",
        "短期借款",
        10_000,
        {"现金流标签": "investment_acquisition_cash"},
    )

    result = classify_pair(pair, EnterpriseType.GENERAL)

    assert "short_term_borrowing_cash_received" in result.tags
    assert "investment_acquisition_cash" not in result.tags
    assert result.supplied_tags == ("investment_acquisition_cash",)
    assert result.conflicts


def test_unspecified_financing_issue_cost_is_kept_as_visible_ambiguity():
    result = classify_pair(
        JournalPair("发行费用", "银行存款", 10_000),
        EnterpriseType.GENERAL,
    )

    assert "financing_issue_cost_cash_paid" in result.tags
    assert "other_financing_cash_paid" in result.tags
    assert result.evidence
