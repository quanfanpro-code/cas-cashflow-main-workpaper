import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def records(name):
    return [json.loads(line) for line in (ROOT / f"references/公式核验/{name}.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def test_three_reference_sets_are_indexed_and_formula_defects_stay_corrected():
    index = (ROOT / "references/依据/来源索引.md").read_text(encoding="utf-8-sig")
    for value in ("第一套", "第二套", "第三套", "知识库", "PDF"):
        assert value in index
    general = records("general_enterprise_v1")
    sales_records = [row for row in general if row["cashflow_item_id"] == "CFO-01"]
    capital_records = [row for row in general if row["cashflow_item_id"] == "CFF-01"]
    assert any(
        issue["type"] == "candidate_range_overlap"
        for row in sales_records
        for issue in row["issues"]
    )
    assert all(row["corrected_formula"]["components"] for row in sales_records)
    assert any(
        issue["type"] == "single_entity_scope"
        for row in capital_records
        for issue in row["issues"]
    )


def test_every_rule_item_has_verified_non_orphan_evidence():
    for rule_path in (ROOT / "rules").glob("*_v1.json"):
        name = rule_path.stem
        registry_name = name
        verified = {row["verification_id"] for row in records(registry_name)}
        raw = json.loads(rule_path.read_text(encoding="utf-8-sig"))
        assert {item["verification_record_id"] for item in raw["items"]} <= verified
