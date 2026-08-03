from pathlib import Path

import openpyxl

from cashflow_main.contracts import EnterpriseType, InputManifest, RunStatus
from cashflow_main.pipeline import RunConfig, prepare_run


def _save(path: Path, headers, rows=()) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _run_general_case(tmp_path: Path, balance_sheet, trial_balance, journal_pairs):
    balance_path = tmp_path / "审定资产负债表.xlsx"
    income_path = tmp_path / "审定利润表.xlsx"
    trial_path = tmp_path / "科目余额表.xlsx"
    journal_path = tmp_path / "一借一贷明细.xlsx"
    prior_path = tmp_path / "上期现金流量表.xlsx"
    _save(balance_path, ["项目", "期末数", "期初数"], balance_sheet)
    _save(income_path, ["项目", "本期数"])
    _save(
        trial_path,
        ["科目编码", "科目名称", "期初余额", "借方发生额", "贷方发生额", "期末余额"],
        trial_balance,
    )
    _save(journal_path, ["借方科目", "贷方科目", "配对金额", "摘要"], journal_pairs)
    _save(prior_path, ["项目", "本期数"])
    config = RunConfig(
        InputManifest(
            balance_path,
            income_path,
            trial_path,
            journal_path,
            prior_path,
            "元",
            "人民币",
            1_000,
        ),
        EnterpriseType.GENERAL,
    )
    return prepare_run(config, tmp_path / "run")


def test_cash_inventory_purchase_input_vat_is_not_counted_as_tax_payment(tmp_path):
    result = _run_general_case(
        tmp_path,
        [("货币资金", 0, 113), ("存货", 100, 0), ("应交税费", 13, 0)],
        [
            ("1002", "银行存款", 113, 0, 113, 0),
            ("1405", "存货", 0, 100, 0, 100),
            ("222101", "应交税费—应交增值税（进项税额）", 0, 13, 0, 13),
        ],
        [
            ("存货", "银行存款", 100, "现购原材料"),
            ("应交税费—应交增值税（进项税额）", "银行存款", 13, "现购原材料进项税"),
        ],
    )

    assert result.status == RunStatus.VALIDATED
    assert result.calculation.by_id["CFO-04"].amount_minor == 11_300
    assert result.calculation.by_id["CFO-06"].amount_minor == 0
    assert result.calculation.by_id["NET-CASH"].amount_minor == -11_300
    assert result.validation_report.cash_change_difference_minor == 0


def test_capex_payable_created_and_settled_in_period_is_investing_cash_only(tmp_path):
    result = _run_general_case(
        tmp_path,
        [("货币资金", 0, 100), ("固定资产", 100, 0), ("应付账款", 0, 0)],
        [
            ("1002", "银行存款", 100, 0, 100, 0),
            ("1601", "固定资产", 0, 100, 0, 100),
            ("2202", "应付账款", 0, 100, 100, 0),
        ],
        [
            ("固定资产", "应付账款", 100, "赊购生产设备"),
            ("应付账款", "银行存款", 100, "支付设备款"),
        ],
    )

    assert result.status == RunStatus.VALIDATED
    assert result.calculation.by_id["CFO-04"].amount_minor == 0
    assert result.calculation.by_id["CFI-06"].amount_minor == 10_000
    assert result.calculation.by_id["NET-CASH"].amount_minor == -10_000
    assert result.validation_report.cash_change_difference_minor == 0


def test_capex_payable_created_but_unpaid_has_no_cashflow(tmp_path):
    result = _run_general_case(
        tmp_path,
        [("货币资金", 100, 100), ("固定资产", 100, 0), ("应付账款", 100, 0)],
        [
            ("1002", "银行存款", 100, 0, 0, 100),
            ("1601", "固定资产", 0, 100, 0, 100),
            ("2202", "应付账款", 0, 0, 100, 100),
        ],
        [("固定资产", "应付账款", 100, "赊购生产设备")],
    )

    assert result.status == RunStatus.VALIDATED
    assert result.calculation.by_id["CFO-04"].amount_minor == 0
    assert result.calculation.by_id["CFI-06"].amount_minor == 0
    assert result.validation_report.cash_change_difference_minor == 0


def test_capex_payable_partial_settlement_uses_actual_cash_amount(tmp_path):
    result = _run_general_case(
        tmp_path,
        [("货币资金", 60, 100), ("固定资产", 100, 0), ("应付账款", 60, 0)],
        [
            ("1002", "银行存款", 100, 0, 40, 60),
            ("1601", "固定资产", 0, 100, 0, 100),
            ("2202", "应付账款", 0, 40, 100, 60),
        ],
        [
            ("固定资产", "应付账款", 100, "赊购生产设备"),
            ("应付账款", "银行存款", 40, "支付部分设备款"),
        ],
    )

    assert result.status == RunStatus.VALIDATED
    assert result.calculation.by_id["CFO-04"].amount_minor == 0
    assert result.calculation.by_id["CFI-06"].amount_minor == 4_000
    assert result.validation_report.cash_change_difference_minor == 0


def test_explicit_engineering_payable_from_prior_period_is_capex_cash(tmp_path):
    result = _run_general_case(
        tmp_path,
        [("货币资金", 0, 100), ("固定资产", 100, 100), ("长期应付款", 0, 100)],
        [
            ("1002", "银行存款", 100, 0, 100, 0),
            ("1601", "固定资产", 100, 0, 0, 100),
            ("2701", "应付工程款", 100, 100, 0, 0),
        ],
        [("应付工程款", "银行存款", 100, "支付上期工程款")],
    )

    assert result.calculation.by_id["CFO-04"].amount_minor == 0
    assert result.calculation.by_id["CFI-06"].amount_minor == 10_000
    assert result.calculation.by_id["CFI-09"].amount_minor == 0
    assert result.validation_report.cash_change_difference_minor == 0


def test_prior_period_generic_payable_with_explicit_engineering_summary_is_capex_cash(tmp_path):
    result = _run_general_case(
        tmp_path,
        [("货币资金", 0, 100), ("固定资产", 100, 100), ("应付账款", 0, 100)],
        [
            ("1002", "银行存款", 100, 0, 100, 0),
            ("1601", "固定资产", 100, 0, 0, 100),
            ("2202", "应付账款", 100, 100, 0, 0),
        ],
        [("应付账款", "银行存款", 100, "支付上期设备工程款")],
    )

    assert result.calculation.by_id["CFO-04"].amount_minor == 0
    assert result.calculation.by_id["CFI-06"].amount_minor == 10_000
    assert result.validation_report.cash_change_difference_minor == 0


def test_capitalized_employee_compensation_accrual_is_noncash(tmp_path):
    result = _run_general_case(
        tmp_path,
        [("货币资金", 100, 100), ("在建工程", 100, 0), ("应付职工薪酬", 100, 0)],
        [
            ("1002", "银行存款", 100, 0, 0, 100),
            ("1604", "在建工程", 0, 100, 0, 100),
            ("2211", "应付职工薪酬", 0, 0, 100, 100),
        ],
        [("在建工程", "应付职工薪酬", 100, "计提工程人员工资")],
    )

    assert result.status == RunStatus.VALIDATED
    assert result.calculation.by_id["CFO-05"].amount_minor == 0
    assert result.calculation.by_id["CFI-06"].amount_minor == 0
    assert result.validation_report.cash_change_difference_minor == 0


def test_capitalized_employee_compensation_paid_in_period_is_capex_cash(tmp_path):
    result = _run_general_case(
        tmp_path,
        [("货币资金", 0, 100), ("在建工程", 100, 0), ("应付职工薪酬", 0, 0)],
        [
            ("1002", "银行存款", 100, 0, 100, 0),
            ("1604", "在建工程", 0, 100, 0, 100),
            ("2211", "应付职工薪酬", 0, 100, 100, 0),
        ],
        [
            ("在建工程", "应付职工薪酬", 100, "计提工程人员工资"),
            ("应付职工薪酬", "银行存款", 100, "支付工程人员工资"),
        ],
    )

    assert result.status == RunStatus.VALIDATED
    assert result.calculation.by_id["CFO-05"].amount_minor == 0
    assert result.calculation.by_id["CFI-06"].amount_minor == 10_000
    assert result.validation_report.cash_change_difference_minor == 0


def test_mixed_generic_payable_source_is_provisional_instead_of_guessed(tmp_path):
    result = _run_general_case(
        tmp_path,
        [
            ("货币资金", 0, 100),
            ("固定资产", 100, 0),
            ("存货", 100, 0),
            ("应付账款", 100, 0),
        ],
        [
            ("1002", "银行存款", 100, 0, 100, 0),
            ("1601", "固定资产", 0, 100, 0, 100),
            ("1405", "存货", 0, 100, 0, 100),
            ("2202", "应付账款", 0, 100, 200, 100),
        ],
        [
            ("固定资产", "应付账款", 100, "赊购生产设备"),
            ("存货", "应付账款", 100, "赊购原材料"),
            ("应付账款", "银行存款", 100, "支付供应商款项"),
        ],
    )

    assert result.status == RunStatus.PROVISIONAL
    assert result.calculation.by_id["CFO-04"].amount_minor == 10_000
    assert result.calculation.by_id["CFI-06"].amount_minor == 0
    assert result.validation_report.cash_change_difference_minor == 0
    assert any(
        case.decision_id.startswith("CAPITAL_PAYMENT:") and case.human_review_required
        for case in result.decision_cases
    )
