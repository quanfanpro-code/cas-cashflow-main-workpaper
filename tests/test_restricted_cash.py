import json
from pathlib import Path

import openpyxl

from cashflow_main.adjustment_bridge import AdjustmentBridgeResult
from cashflow_main.contracts import (
    AccountBalance,
    EnterpriseType,
    InputManifest,
    NormalizedInputBundle,
    RunStatus,
)
from cashflow_main.fact_extraction import cash_and_equivalent_control, extract_facts
from cashflow_main.pipeline import RunConfig, prepare_run


CASH_GROUPS = {
    "cash_and_equivalents": ["库存现金", "银行存款", "其他货币资金", "现金等价物"],
}


def save(path: Path, headers, rows=()):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_restricted_cash_facts_are_excluded_from_both_control_balances():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(
            AccountBalance("100201", "银行存款", 10_000, 5_000, 0, 15_000),
            AccountBalance("100202", "银行存款-冻结资金", 2_000, 0, 0, 2_000),
            AccountBalance("100203", "银行存款-履约保证金", 1_000, 0, 0, 1_000),
        ),
        journal_pairs=(),
        prior_cashflow=(),
    )
    facts = extract_facts(
        bundle,
        AdjustmentBridgeResult((), (), True),
        EnterpriseType.GENERAL,
        CASH_GROUPS,
    )

    opening, closing, restricted = cash_and_equivalent_control(facts)

    assert opening == 10_000
    assert closing == 15_000
    assert {fact.metadata["account_name"] for fact in restricted} == {
        "银行存款-冻结资金",
        "银行存款-履约保证金",
    }
    assert {fact.metadata["kind"] for fact in restricted} == {"opening", "closing"}
    assert len(restricted) == 4


def test_duplicate_or_blank_account_codes_do_not_overwrite_fact_evidence():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(
            AccountBalance("", "银行存款-基本户", 10_000, 0, 0, 10_000),
            AccountBalance("", "银行存款-一般户", 20_000, 0, 0, 20_000),
        ),
        journal_pairs=(),
        prior_cashflow=(),
    )

    facts = extract_facts(
        bundle,
        AdjustmentBridgeResult((), (), True),
        EnterpriseType.GENERAL,
        CASH_GROUPS,
    )

    closing = [
        fact
        for fact in facts.values()
        if "cash_equivalent" in fact.tags and fact.metadata.get("kind") == "closing"
    ]
    assert len(closing) == 2
    assert len({fact.fact_id for fact in closing}) == 2


def test_pipeline_cash_balance_items_use_restricted_cash_control(tmp_path):
    bs = tmp_path / "资产负债表.xlsx"
    save(bs, ["项目", "期末数", "期初数"], [("货币资金", 170, 120)])
    income = tmp_path / "利润表.xlsx"
    save(income, ["项目", "本期数"], [("营业收入", 50)])
    tb = tmp_path / "余额表.xlsx"
    save(tb, ["科目编码", "科目名称", "期初余额", "借方发生额", "贷方发生额", "期末余额"], [
        ("100201", "银行存款", 100, 50, 0, 150),
        ("100202", "银行存款-冻结资金", 20, 0, 0, 20),
        ("6001", "营业收入", 0, 0, 50, 50),
    ])
    journal = tmp_path / "一借一贷.xlsx"
    save(journal, ["借方科目", "贷方科目", "配对金额"], [("银行存款", "营业收入", 50)])
    prior = tmp_path / "上期现流.xlsx"
    save(prior, ["项目", "本期数"])
    config = RunConfig(InputManifest(bs, income, tb, journal, prior, "元", "人民币", 1_000), EnterpriseType.GENERAL)

    result = prepare_run(config, tmp_path / "run")

    assert result.status == RunStatus.VALIDATED
    assert result.calculation.by_id["OPENING-CASH"].amount_minor == 10_000
    assert result.calculation.by_id["CLOSING-CASH"].amount_minor == 15_000


def test_material_undetailed_other_monetary_funds_stays_provisional(tmp_path):
    bs = tmp_path / "资产负债表.xlsx"
    save(bs, ["项目", "期末数", "期初数"], [("货币资金", 10, 10)])
    income = tmp_path / "利润表.xlsx"
    save(income, ["项目", "本期数"], [("营业收入", 0)])
    tb = tmp_path / "余额表.xlsx"
    save(tb, ["科目编码", "科目名称", "期初余额", "借方发生额", "贷方发生额", "期末余额"], [
        ("1012", "其他货币资金", 10, 0, 0, 10),
    ])
    journal = tmp_path / "一借一贷.xlsx"
    save(journal, ["借方科目", "贷方科目", "配对金额"])
    prior = tmp_path / "上期现流.xlsx"
    save(prior, ["项目", "本期数"])
    config = RunConfig(InputManifest(bs, income, tb, journal, prior, "元", "人民币", 1_000), EnterpriseType.GENERAL)

    result = prepare_run(config, tmp_path / "run")

    assert result.status == RunStatus.PROVISIONAL
    assert any(case.decision_id.startswith("RESTRICTED_CASH:") for case in result.decision_cases)


def test_long_term_deposit_is_excluded_from_cash_equivalents():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(
            AccountBalance("100299", "银行存款-一年期定期存款", 10_000, 0, 0, 10_000),
        ),
        journal_pairs=(),
        prior_cashflow=(),
    )
    facts = extract_facts(
        bundle,
        AdjustmentBridgeResult((), (), True),
        EnterpriseType.GENERAL,
        CASH_GROUPS,
    )

    opening, closing, excluded = cash_and_equivalent_control(facts)

    assert opening == 0
    assert closing == 0
    assert {fact.metadata["account_name"] for fact in excluded} == {
        "银行存款-一年期定期存款",
    }
    assert all("non_cash_equivalent" in fact.tags for fact in excluded)


def test_undetailed_term_deposit_is_not_silently_included():
    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(
            AccountBalance("100298", "银行定期存款", 10_000, 0, 0, 10_000),
        ),
        journal_pairs=(),
        prior_cashflow=(),
    )
    facts = extract_facts(
        bundle,
        AdjustmentBridgeResult((), (), True),
        EnterpriseType.GENERAL,
        CASH_GROUPS,
    )

    opening, closing, _ = cash_and_equivalent_control(facts)

    assert opening == 0
    assert closing == 0
    assert any("cash_equivalent_uncertain" in fact.tags for fact in facts.values())


def _general_rule_pack():
    registry = {}
    registry_path = Path("references/公式核验/general_enterprise_v1.jsonl")
    for line in registry_path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            record = json.loads(line)
            registry[str(record["verification_id"])] = record
    from cashflow_main.rule_loader import load_rule_pack

    return load_rule_pack(Path("rules/general_enterprise_v1.json"), registry)


def test_cash_balance_items_ignore_long_term_and_undetailed_deposits():
    from cashflow_main.item_calculators import calculate_items

    bundle = NormalizedInputBundle(
        audited_balance_sheet=(),
        audited_income_statement=(),
        trial_balance=(
            AccountBalance("100201", "银行存款", 10_000, 0, 0, 10_000),
            AccountBalance("100298", "银行存款-一年期定期存款", 5_000, 0, 0, 5_000),
            AccountBalance("100299", "银行存款-定期存款", 3_000, 0, 0, 3_000),
            AccountBalance("100202", "银行存款-冻结资金", 2_000, 0, 0, 2_000),
        ),
        journal_pairs=(),
        prior_cashflow=(),
    )
    facts = extract_facts(
        bundle,
        AdjustmentBridgeResult((), (), True),
        EnterpriseType.GENERAL,
        CASH_GROUPS,
    )
    calculation = calculate_items(_general_rule_pack(), facts)

    assert calculation.by_id["OPENING-CASH"].amount_minor == 10_000
    assert calculation.by_id["CLOSING-CASH"].amount_minor == 10_000
