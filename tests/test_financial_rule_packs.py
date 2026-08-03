import json
import sys
from pathlib import Path

import pytest

from cashflow_main.rule_loader import load_rule_pack

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_formula_registry import load_registry  # noqa: E402


@pytest.mark.parametrize(
    ("industry", "required_item"),
    [
        ("bank", "客户存款和同业存放款项净增加额"),
        ("securities", "代理买卖证券收到的现金净额"),
        ("insurance", "收到原保险合同保费取得的现金"),
        ("other_financial", "收到其他与经营活动有关的现金"),
    ],
)
def test_financial_pack_has_verified_industry_items(industry, required_item):
    registry = load_registry(ROOT / f"references/公式核验/{industry}_v1.jsonl")
    pack = load_rule_pack(
        ROOT / f"rules/{industry}_v1.json", verification_ids=set(registry)
    )
    assert required_item in {item.name for item in pack.items}
    assert {item.verification_record_id for item in pack.items} <= set(registry)
    assert all(record["conclusion"] == "verified" for record in registry.values())


@pytest.mark.parametrize("industry", ["bank", "securities", "insurance"])
def test_financial_net_items_declare_basis_and_mutual_exclusion(industry):
    raw = json.loads(
        (ROOT / f"rules/{industry}_v1.json").read_text(encoding="utf-8-sig")
    )
    registry = load_registry(ROOT / f"references/公式核验/{industry}_v1.jsonl")
    net_items = [
        item
        for item in raw["items"]
        if any(c["gross_or_net"] == "net" for c in item["components"])
        and not item.get("is_subtotal")
    ]
    assert net_items
    for item in net_items:
        record = registry[item["verification_record_id"]]
        assert record["netting_basis"]
        assert record["mutual_exclusion"]


def test_industry_specific_items_are_isolated_from_general_sales_rule():
    for industry in ("bank", "securities", "insurance"):
        raw = json.loads(
            (ROOT / f"rules/{industry}_v1.json").read_text(encoding="utf-8-sig")
        )
        assert "销售商品、提供劳务收到的现金" not in {
            item["name"] for item in raw["items"]
        }


@pytest.mark.parametrize("industry", ["bank", "securities", "insurance", "other_financial"])
def test_financial_packs_use_official_common_outflow_and_separate_bond_line(industry):
    raw = json.loads((ROOT / f"rules/{industry}_v1.json").read_text(encoding="utf-8-sig"))
    names = {item["name"] for item in raw["items"]}
    assert {
        "为交易目的而持有的金融资产净增加额",
        "拆出资金净增加额",
        "发行债券收到的现金",
    } <= names
    borrowing = next(item for item in raw["items"] if item["name"] == "取得借款收到的现金")
    assert all(
        "bond_issue_cash_received" not in component["selector"].get("tags_any", [])
        for component in borrowing["components"]
    )


def test_securities_uses_2018_direction_and_has_reverse_repo_outflow():
    raw = json.loads((ROOT / "rules/securities_v1.json").read_text(encoding="utf-8-sig"))
    names = {item["name"] for item in raw["items"]}
    assert "处置交易性金融资产净增加额" not in names
    assert "返售业务资金净增加额" in names
    trading = next(item for item in raw["items"] if item["name"] == "为交易目的而持有的金融资产净增加额")
    outflow = next(item for item in raw["items"] if item["item_id"] == "CFO-OUT")
    assert trading["item_id"] in outflow["subtotal_of"]
    assert {"融出资金净减少额", "融出资金净增加额", "返售业务资金净减少额"} <= names


def test_old_insurance_splits_reinsurance_and_policyholder_net_outflows():
    raw = json.loads((ROOT / "rules/insurance_v1.json").read_text(encoding="utf-8-sig"))
    names = {item["name"] for item in raw["items"]}
    assert "支付再保业务现金净额" in names
    assert "保户储金及投资款净减少额" in names
    assert "支付手续费及佣金的现金" in names
    assert "支付利息、手续费及佣金的现金" not in names
    split_items = [
        item for item in raw["items"]
        if item["name"] in {
            "收到再保业务现金净额", "支付再保业务现金净额",
            "保户储金及投资款净增加额", "保户储金及投资款净减少额",
        }
    ]
    assert all(item["components"][0]["operation"] == "net_fact_amount" for item in split_items)


def test_other_financial_removes_unofficial_fixed_names():
    raw = json.loads((ROOT / "rules/other_financial_v1.json").read_text(encoding="utf-8-sig"))
    names = {item["name"] for item in raw["items"]}
    assert "收到业务经营活动取得的现金" not in names
    assert "支付业务经营活动的现金" not in names
    assert "收取利息、手续费及佣金的现金" in names


def test_insurance_2023_format_uses_new_contract_names():
    raw = json.loads((ROOT / "rules/insurance_2023_v1.json").read_text(encoding="utf-8-sig"))
    names = {item["name"] for item in raw["items"]}
    assert "收到签发保险合同保费取得的现金" in names
    assert "收到分入再保险合同的现金净额" in names
    assert "支付签发保险合同赔款的现金" in names
    assert "支付分出再保险合同的现金净额" in names
    assert "收到原保险合同保费取得的现金" not in names


def test_current_insurance_pack_has_item_level_verification_records():
    raw = json.loads(
        (ROOT / "rules/insurance_2023_v1.json").read_text(encoding="utf-8-sig")
    )
    records = {
        record["verification_id"]: record
        for line in (ROOT / "references/公式核验/insurance_2023_v1.jsonl")
        .read_text(encoding="utf-8-sig")
        .splitlines()
        if line.strip()
        for record in (json.loads(line),)
    }

    assert len(records) == len(raw["items"])
    assert len({item["verification_record_id"] for item in raw["items"]}) == len(
        raw["items"]
    )
    for item in raw["items"]:
        record = records[item["verification_record_id"]]
        assert record["cashflow_item_id"] == item["item_id"]
        assert item["name"] in record["candidate_formula"]["components"]


@pytest.mark.parametrize(
    "industry",
    ["bank", "securities", "insurance", "insurance_2023", "other_financial"],
)
def test_financial_capex_uses_actual_cash_facts_and_routes_business_net_cash(industry):
    raw = json.loads((ROOT / f"rules/{industry}_v1.json").read_text(encoding="utf-8-sig"))
    by_id = {item["item_id"]: item for item in raw["items"]}

    capex = by_id["CFI-06"]
    assert all(component["operation"] != "balance_change" for component in capex["components"])
    capex_tags = {
        tag
        for component in capex["components"]
        for tag in component["selector"].get("tags_any", [])
    }
    assert {
        "long_lived_asset_cash_addition",
        "long_lived_asset_input_tax_cash",
        "capex_payable_cash_paid",
        "capex_employee_cash_paid",
    } <= capex_tags

    investing_other_tags = {
        tag
        for item_id in ("CFI-05", "CFI-09")
        for component in by_id[item_id]["components"]
        for key in ("tags_any", "positive_tags_any", "negative_tags_any")
        for tag in component["selector"].get(key, [])
    }
    assert {
        "business_disposal_cash_received",
        "prior_business_disposal_cash_received",
        "disposed_business_cash_and_equivalents",
        "business_disposal_cost_cash_paid",
        "business_acquisition_cash_paid",
        "prior_business_acquisition_cash_paid",
        "acquired_business_cash_and_equivalents",
    } <= investing_other_tags
