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


def test_investment_property_purchase_is_long_lived_asset_not_investment():
    result = classify_pair(
        JournalPair("投资性房地产", "银行存款", 10_000),
        EnterpriseType.GENERAL,
    )

    assert "long_lived_asset_cash_addition" in result.tags
    assert "investment_acquisition_cash" not in result.tags


def test_fixed_asset_clearance_payment_is_disposal_cost_not_asset_purchase():
    result = classify_pair(
        JournalPair("固定资产清理", "银行存款", 10_000),
        EnterpriseType.GENERAL,
    )

    assert "long_lived_asset_disposal_cost_cash" in result.tags
    assert "long_lived_asset_cash_addition" not in result.tags


def test_fixed_asset_disposal_receipt_is_classified_without_external_label():
    result = classify_pair(
        JournalPair("银行存款", "固定资产清理", 10_000),
        EnterpriseType.GENERAL,
    )

    assert "long_lived_asset_disposal_cash" in result.tags
    assert not result.supplied_tags


def test_government_grant_receipt_uses_summary_evidence():
    result = classify_pair(
        JournalPair(
            "银行存款",
            "其他收益",
            10_000,
            {"摘要": "收到稳岗政府补助"},
        ),
        EnterpriseType.GENERAL,
    )

    assert "government_grant_receipt" in result.tags


def test_routine_sales_receipt_is_marked_as_workpaper_formula_covered():
    result = classify_pair(
        JournalPair("银行存款", "应收账款", 10_000),
        EnterpriseType.GENERAL,
    )

    assert "workpaper_formula_covered" in result.tags
    assert "other_operating_cash_received" not in result.tags


def test_cash_account_transfer_is_not_a_cash_flow():
    result = classify_pair(
        JournalPair("银行存款", "库存现金", 10_000),
        EnterpriseType.GENERAL,
    )

    assert result.tags == ("cash_account_transfer",)


def test_unknown_cash_receipt_has_activity_candidates_and_operating_preference():
    result = classify_pair(
        JournalPair("银行存款", "往来科目", 10_000),
        EnterpriseType.GENERAL,
    )

    assert result.preferred_tag == "other_operating_receipt"
    assert set(result.candidate_tags) == {
        "other_operating_receipt",
        "other_investing_cash_received",
        "other_financing_cash_received",
    }
    assert result.strong_conflict is True


@pytest.mark.parametrize(
    ("debit", "credit", "summary", "expected_tag"),
    [
        ("银行存款", "应交税费", "收到增值税留抵退税", "cash_tax_refund"),
        ("银行存款", "其他业务收入", "收到经营租赁租金", "operating_lease_receipt"),
        ("银行存款", "长期股权投资", "处置子公司收到价款", "business_disposal_cash_received"),
        ("长期股权投资", "银行存款", "取得子公司支付价款", "business_acquisition_cash_paid"),
        ("投资收益", "银行存款", "支付投资交易手续费", "investment_transaction_cost_cash"),
        ("财务费用", "银行存款", "支付资本化借款利息", "capitalized_interest_cash"),
        ("管理费用", "应付职工薪酬", "计提管理人员工资", "employee_compensation_expense"),
        ("应付职工薪酬", "库存商品", "发放非货币性福利", "noncash_employee_benefit"),
        ("在建工程", "应付职工薪酬", "计提工程人员工资", "capex_employee_cash"),
        ("在建工程", "应付账款", "确认应付工程设备款", "capex_payable_change"),
        ("预付工程款", "银行存款", "预付设备采购款", "capex_prepayment_change"),
        ("银行存款", "汇兑损益", "外币现金汇率变动", "cash_exchange_effect"),
        ("应交税费-待抵扣进项税", "银行存款", "支付购建设备进项税", "long_lived_asset_input_tax_cash"),
        ("库存商品", "实收资本", "股东以商品投资", "inventory_noncash_increase"),
        ("固定资产", "应收账款", "以固定资产抵偿经营应收款", "noncash_receivable_settlement"),
        ("其他应收款", "应收账款", "经营应收转为非经营性往来", "non_operating_receivable_change"),
    ],
)
def test_current_workpaper_adjustment_tags_are_inferred_from_accounts_and_summary(
    debit, credit, summary, expected_tag
):
    result = classify_pair(
        JournalPair(debit, credit, 10_000, {"摘要": summary}),
        EnterpriseType.GENERAL,
    )

    assert expected_tag in result.tags


def test_non_operating_receivable_transfer_is_not_counted_again_as_generic_noncash_settlement():
    result = classify_pair(
        JournalPair(
            "其他应收款",
            "应收账款",
            10_000,
            {"摘要": "经营应收转为非经营性往来"},
        ),
        EnterpriseType.GENERAL,
    )

    assert "non_operating_receivable_change" in result.tags
    assert "noncash_receivable_settlement" not in result.tags
