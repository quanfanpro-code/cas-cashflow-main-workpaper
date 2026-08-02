import json
from pathlib import Path

import openpyxl

from cashflow_main.contracts import EnterpriseType, InputManifest, RunStatus
from cashflow_main.pipeline import RunConfig, finalize_run, load_review_context, prepare_run


def save(path: Path, headers, rows=()):
    wb = openpyxl.Workbook(); ws = wb.active; ws.append(headers)
    for row in rows: ws.append(row)
    wb.save(path)


def test_confirming_every_pending_adjustment_promotes_provisional_to_final(tmp_path):
    bs = tmp_path / "资产负债表.xlsx"; save(bs, ["项目", "期末数", "期初数"], [("货币资金", 0, 0), ("存货", 10, 0)])
    income = tmp_path / "利润表.xlsx"; save(income, ["项目", "本期数"], [("营业收入", 0)])
    tb = tmp_path / "余额表.xlsx"; save(tb, ["科目编码", "科目名称", "期初余额", "借方发生额", "贷方发生额", "期末余额"], [("1002", "银行存款", 0, 0, 0, 0)])
    journal = tmp_path / "一借一贷.xlsx"; save(journal, ["借方科目", "贷方科目", "配对金额"])
    prior = tmp_path / "上期现流.xlsx"; save(prior, ["项目", "本期数"])
    config = RunConfig(InputManifest(bs, income, tb, journal, prior, "元", "人民币", 100), EnterpriseType.GENERAL)
    prepared = prepare_run(config, tmp_path / "run")
    assert prepared.status == RunStatus.PROVISIONAL
    state = json.loads((prepared.run_dir / "state.json").read_text(encoding="utf-8-sig"))
    decisions = [{"decision_id": value, "confirmed": True} for value in state["pending_decisions"]]
    final = finalize_run(prepared.run_dir, decisions)
    assert final.status == RunStatus.VALIDATED
    assert final.statement_kind == "最终"
    rows = load_review_context(prepared.run_dir)["decision_cases"]
    assert all(
        row.get("confirmation_status") in {"已确认", "无需确认"}
        for row in rows
    )
