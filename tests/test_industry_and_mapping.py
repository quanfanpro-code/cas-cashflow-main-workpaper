from cashflow_main.contracts import (
    AccountBalance,
    EnterpriseType,
    NormalizedInputBundle,
    StatementLine,
)
from cashflow_main.industry_detection import detect_enterprise_type, detect_insurance_format
from cashflow_main.statement_mapping import (
    MappingRule,
    StatementMapping,
    build_book_statements,
    with_exact_statement_names,
)


def bundle_with(*item_names: str) -> NormalizedInputBundle:
    lines = tuple(StatementLine(name, 0, 0) for name in item_names)
    return NormalizedInputBundle(
        audited_balance_sheet=lines,
        audited_income_statement=(),
        trial_balance=(),
        journal_pairs=(),
        prior_cashflow=(),
    )


def test_bank_is_detected_from_joint_statement_evidence():
    result = detect_enterprise_type(
        bundle_with("客户贷款及垫款", "吸收存款及同业存放")
    )
    assert result.preferred == EnterpriseType.BANK
    assert result.requires_confirmation is False


def test_ambiguous_financial_type_returns_ranked_candidates():
    result = detect_enterprise_type(bundle_with("客户贷款及垫款", "融出资金"))
    assert result.requires_confirmation is True
    assert result.candidates[0].score >= result.candidates[1].score


def test_other_financial_can_reach_automatic_detection_threshold():
    result = detect_enterprise_type(
        bundle_with("应收融资租赁款", "长期应收款")
    )
    assert result.preferred == EnterpriseType.OTHER_FINANCIAL
    assert result.requires_confirmation is False


def test_trial_balance_builds_reproducible_book_statements():
    rows = (
        AccountBalance(
            account_code="1002",
            account_name="银行存款",
            opening_balance_minor=1_000_000,
            debit_turnover_minor=700_000,
            credit_turnover_minor=200_000,
            closing_balance_minor=1_500_000,
        ),
    )
    mapping = StatementMapping(
        (
            MappingRule(
                report_item="货币资金",
                statement="balance_sheet",
                account_code_prefixes=("1002",),
                amount_mode="balance",
            ),
        )
    )
    statements = build_book_statements(rows, mapping)
    assert statements.balance_sheet["货币资金"].current_minor == 1_500_000
    assert statements.balance_sheet["货币资金"].prior_minor == 1_000_000
    assert statements.unmapped_amount_minor == 0


def test_exact_account_name_mapping_supports_full_statement_items():
    rows = (
        AccountBalance(
            account_code="1122",
            account_name="应收账款",
            opening_balance_minor=800_000,
            debit_turnover_minor=500_000,
            credit_turnover_minor=400_000,
            closing_balance_minor=900_000,
        ),
    )
    mapping = StatementMapping(
        (
            MappingRule(
                report_item="应收账款",
                statement="balance_sheet",
                amount_mode="balance",
                account_name_equals=("应收账款",),
            ),
        )
    )
    statements = build_book_statements(rows, mapping)
    assert statements.balance_sheet["应收账款"].current_minor == 900_000


def test_audited_statement_names_map_detailed_subaccounts_to_parent_item():
    rows = (
        AccountBalance("112201", "应收账款—甲客户", 100, 20, 10, 110),
        AccountBalance("112202", "应收账款—乙客户", 200, 30, 20, 210),
    )
    mapping = with_exact_statement_names(StatementMapping(()), ["应收账款"], [])

    statements = build_book_statements(rows, mapping)

    assert statements.balance_sheet["应收账款"].current_minor == 320
    assert not statements.unmapped_accounts


def test_exact_specific_statement_item_wins_over_parent_contains_mapping():
    mapping = with_exact_statement_names(
        StatementMapping(()), ["固定资产", "固定资产清理"], []
    )
    row = AccountBalance("1606", "固定资产清理", 0, 0, 0, 10)

    assert mapping.unique_match(row).report_item == "固定资产清理"


def test_same_mapping_result_from_code_and_exact_name_is_not_treated_as_conflict():
    row = AccountBalance(
        account_code="1002",
        account_name="银行存款",
        opening_balance_minor=0,
        debit_turnover_minor=0,
        credit_turnover_minor=0,
        closing_balance_minor=0,
    )
    mapping = StatementMapping(
        (
            MappingRule("货币资金", "balance_sheet", "balance", account_code_prefixes=("1002",)),
            MappingRule("货币资金", "balance_sheet", "balance", account_name_equals=("银行存款",)),
        )
    )
    assert mapping.unique_match(row).report_item == "货币资金"


def test_insurance_format_is_detected_from_new_and_old_statement_terms():
    assert detect_insurance_format(bundle_with("保险合同负债", "保险服务收入"), "2025年度") == "insurance_2023"
    assert detect_insurance_format(bundle_with("未到期责任准备金", "保险业务收入"), "2022年度") == "insurance_2018"
    assert detect_insurance_format(bundle_with("货币资金"), "2024年度") is None
