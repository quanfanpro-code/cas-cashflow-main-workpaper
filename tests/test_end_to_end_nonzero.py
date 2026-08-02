from pathlib import Path

import openpyxl

from cashflow_main.contracts import EnterpriseType, InputManifest, RunStatus
from cashflow_main.pipeline import RunConfig, prepare_run


def save(path: Path, headers, rows=()):
    wb = openpyxl.Workbook(); ws = wb.active; ws.append(headers)
    for row in rows: ws.append(row)
    wb.save(path)


def test_nonzero_cash_sale_flows_through_workpaper_and_cash_control(tmp_path):
    bs = tmp_path / "资产负债表.xlsx"; save(bs, ["项目", "期末数", "期初数"], [("货币资金", 100, 0)])
    income = tmp_path / "利润表.xlsx"; save(income, ["项目", "本期数"], [("营业收入", 100)])
    tb = tmp_path / "余额表.xlsx"; save(tb, ["科目编码", "科目名称", "期初余额", "借方发生额", "贷方发生额", "期末余额"], [("1002", "银行存款", 0, 100, 0, 100), ("6001", "主营业务收入", 0, 0, 100, 100)])
    journal = tmp_path / "一借一贷.xlsx"; save(journal, ["借方科目", "贷方科目", "配对金额"], [("银行存款", "主营业务收入", 100)])
    prior = tmp_path / "上期现流.xlsx"; save(prior, ["项目", "本期数"])
    config = RunConfig(InputManifest(bs, income, tb, journal, prior, "元", "人民币", 100), EnterpriseType.GENERAL)
    result = prepare_run(config, tmp_path / "run")
    assert result.status == RunStatus.VALIDATED
    assert result.calculation.by_id["CFO-01"].amount_minor == 10_000
    assert result.calculation.by_id["CFO-NET"].amount_minor == 10_000
    assert result.calculation.by_id["NET-CASH"].amount_minor == 10_000
    assert result.calculation.by_id["CLOSING-CASH"].amount_minor == 10_000
