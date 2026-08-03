import json

import pytest

from cashflow_main.rule_loader import load_rule_pack


def write_rule_file(tmp_path, *, operation="statement_value", verification_id="FV-G-001", selector=None):
    path = tmp_path / "general_enterprise_v1.json"
    path.write_text(
        json.dumps(
            {
                "version": "1",
                "enterprise_type": "general",
                "statement_template": [
                    {
                        "item_id": "CFO-01",
                        "name": "销售商品、提供劳务收到的现金",
                        "section": "operating",
                        "display_order": 10,
                    }
                ],
                "account_groups": {},
                "items": [
                    {
                        "item_id": "CFO-01",
                        "name": "销售商品、提供劳务收到的现金",
                        "section": "operating",
                        "display_order": 10,
                        "verification_record_id": verification_id,
                        "components": [
                            {
                                "component_id": "CFO-01-C01",
                                "operation": operation,
                                "sign": 1,
                                "source_scope": "audited_income_statement",
                                "selector": selector or {"item_id": "营业收入"},
                                "occupancy_policy": "exclusive",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_rule_pack_rejects_executable_formula(tmp_path):
    path = write_rule_file(tmp_path, operation="eval")
    with pytest.raises(ValueError, match="不允许的操作"):
        load_rule_pack(path, verification_ids={"FV-G-001"})


@pytest.mark.parametrize(
    "operation",
    ["debit_turnover", "credit_turnover", "paired_turnover", "adjustment_amount"],
)
def test_rule_pack_rejects_unused_legacy_operations(tmp_path, operation):
    path = write_rule_file(tmp_path, operation=operation)
    with pytest.raises(ValueError, match="不允许的操作"):
        load_rule_pack(path, verification_ids={"FV-G-001"})


def test_rule_pack_rejects_unverified_formula(tmp_path):
    path = write_rule_file(tmp_path, verification_id="FV-MISSING")
    with pytest.raises(ValueError, match="未核验公式"):
        load_rule_pack(path, verification_ids={"FV-G-001"})


def test_rule_pack_accepts_declared_operation(tmp_path):
    path = write_rule_file(tmp_path)
    pack = load_rule_pack(path, verification_ids={"FV-G-001"})
    assert pack.enterprise_type.value == "general"
    assert pack.items[0].components[0].operation == "statement_value"


def test_net_fact_operation_requires_both_directions(tmp_path):
    path = write_rule_file(
        tmp_path,
        operation="net_fact_amount",
        selector={"positive_tags_any": ["received"]},
    )
    with pytest.raises(ValueError, match="正向和负向标签"):
        load_rule_pack(path, verification_ids={"FV-G-001"})


def test_rule_pack_rejects_statement_template_drift(tmp_path):
    path = write_rule_file(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["statement_template"][0]["name"] = "错误名称"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="模板与计算项目属性不一致"):
        load_rule_pack(path, verification_ids={"FV-G-001"})


@pytest.mark.parametrize(
    "record",
    [
        {"conclusion": "unresolved", "issues": []},
        {"conclusion": "verified", "issues": [{"type": "open", "resolution": ""}]},
    ],
)
def test_rule_pack_rejects_incomplete_verification_record(tmp_path, record):
    path = write_rule_file(tmp_path)
    with pytest.raises(ValueError, match="核验未完成"):
        load_rule_pack(path, verification_ids={"FV-G-001": record})
