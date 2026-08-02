from pathlib import Path

import openpyxl
import pytest

from cashflow_main.contracts import EnterpriseType, InputManifest, RunStatus
from cashflow_main.pipeline import RunConfig, prepare_run


def save(path: Path, headers, rows=()):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)


def build_case(tmp_path, *, enterprise_type, period, opening_cash, closing_cash, other_name, other_opening, other_debit, other_credit, other_closing, debit_name, credit_name, amount, statement):
    bs = tmp_path / "资产负债表.xlsx"
    bs_rows = [("货币资金", closing_cash, opening_cash)]
    if statement == "balance_sheet":
        bs_rows.append((other_name, other_closing, other_opening))
    save(bs, ["项目", "期末数", "期初数"], bs_rows)
    income = tmp_path / "利润表.xlsx"
    income_rows = [(other_name, other_closing)] if statement == "income_statement" else []
    save(income, ["项目", "本期数"], income_rows)
    cash_debit = amount if debit_name == "银行存款" else 0
    cash_credit = amount if credit_name == "银行存款" else 0
    tb = tmp_path / "科目余额表.xlsx"
    save(tb, ["科目编码", "科目名称", "期初余额", "借方发生额", "贷方发生额", "期末余额"], [
        ("1002", "银行存款", opening_cash, cash_debit, cash_credit, closing_cash),
        ("9001", other_name, other_opening, other_debit, other_credit, other_closing),
    ])
    journal = tmp_path / "一借一贷.xlsx"
    save(journal, ["借方科目", "贷方科目", "配对金额"], [(debit_name, credit_name, amount)])
    prior = tmp_path / "上期现流.xlsx"
    save(prior, ["项目", "本期数"])
    return RunConfig(
        InputManifest(bs, income, tb, journal, prior, "元", "人民币", 100_000),
        enterprise_type=enterprise_type,
        period=period,
    )


@pytest.mark.parametrize(
    ("enterprise_type", "period", "opening_cash", "closing_cash", "other_name", "other_opening", "other_debit", "other_credit", "other_closing", "debit_name", "credit_name", "amount", "statement", "item_id", "expected"),
    [
        (EnterpriseType.BANK, "2025年度", 0, 100, "吸收存款及同业存放", 0, 0, 100, 100, "银行存款", "吸收存款及同业存放", 100, "balance_sheet", "CFO-B01", 10_000),
        (EnterpriseType.SECURITIES, "2025年度", 100, 60, "交易性金融资产", 0, 40, 0, 40, "交易性金融资产", "银行存款", 40, "balance_sheet", "CFO-S-TRADING", 4_000),
        (EnterpriseType.INSURANCE, "2022年度", 0, 50, "保险业务收入", 0, 0, 50, 50, "银行存款", "保险业务收入", 50, "income_statement", "CFO-I01", 5_000),
        (EnterpriseType.INSURANCE, "2025年度", 0, 60, "保险合同负债", 0, 0, 60, 60, "银行存款", "保险合同负债", 60, "balance_sheet", "CFO-I23-PREMIUM", 6_000),
        (EnterpriseType.OTHER_FINANCIAL, "2025年度", 0, 30, "手续费及佣金收入", 0, 0, 30, 30, "银行存款", "手续费及佣金收入", 30, "income_statement", "CFO-O-INTEREST", 3_000),
        (EnterpriseType.INSURANCE, "2022年度", 100, 60, "应付分保账款", 0, 40, 0, -40, "应付分保账款", "银行存款", 40, "balance_sheet", "CFO-I08", 4_000),
        (EnterpriseType.INSURANCE, "2022年度", 100, 60, "保户储金及投资款", 40, 40, 0, 0, "保户储金及投资款", "银行存款", 40, "balance_sheet", "CFO-I-POLICY-OUT", 4_000),
        (EnterpriseType.SECURITIES, "2025年度", 100, 60, "融出资金", 0, 40, 0, 40, "融出资金", "银行存款", 40, "balance_sheet", "CFO-S-MARGIN-INCREASE", 4_000),
        (EnterpriseType.SECURITIES, "2025年度", 0, 40, "买入返售金融资产", 40, 0, 40, 0, "银行存款", "买入返售金融资产", 40, "balance_sheet", "CFO-S-REVERSE-REPO-DECREASE", 4_000),
    ],
)
def test_financial_industry_nonzero_item_runs_full_pipeline(
    tmp_path,
    enterprise_type,
    period,
    opening_cash,
    closing_cash,
    other_name,
    other_opening,
    other_debit,
    other_credit,
    other_closing,
    debit_name,
    credit_name,
    amount,
    statement,
    item_id,
    expected,
):
    config = build_case(
        tmp_path,
        enterprise_type=enterprise_type,
        period=period,
        opening_cash=opening_cash,
        closing_cash=closing_cash,
        other_name=other_name,
        other_opening=other_opening,
        other_debit=other_debit,
        other_credit=other_credit,
        other_closing=other_closing,
        debit_name=debit_name,
        credit_name=credit_name,
        amount=amount,
        statement=statement,
    )
    result = prepare_run(config, tmp_path / "run")

    assert result.status == RunStatus.VALIDATED
    assert result.calculation.by_id[item_id].amount_minor == expected
    expected_change = (closing_cash - opening_cash) * 100
    assert result.calculation.by_id["NET-CASH"].amount_minor == expected_change
    assert result.validation_report.cash_change_difference_minor == 0
