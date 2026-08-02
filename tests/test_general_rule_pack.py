import json
import sys
from pathlib import Path

from cashflow_main.rule_loader import load_rule_pack
from cashflow_main.fact_extraction import Fact, FactLedger
from cashflow_main.item_calculators import calculate_items

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_formula_registry import load_registry  # noqa: E402

EXPECTED_DETAIL_IDS = {
    "CFO-01",
    "CFO-02",
    "CFO-03",
    "CFO-04",
    "CFO-05",
    "CFO-06",
    "CFO-07",
    "CFI-01",
    "CFI-02",
    "CFI-03",
    "CFI-04",
    "CFI-05",
    "CFI-06",
    "CFI-07",
    "CFI-08",
    "CFI-09",
    "CFF-01",
    "CFF-02",
    "CFF-03",
    "CFF-04",
    "CFF-05",
    "CFF-06",
}


def test_general_pack_has_verified_record_for_every_item():
    registry = load_registry(
        ROOT / "references/公式核验/general_enterprise_v1.jsonl"
    )
    pack = load_rule_pack(
        ROOT / "rules/general_enterprise_v1.json",
        verification_ids=set(registry),
    )
    assert EXPECTED_DETAIL_IDS <= {item.item_id for item in pack.items}
    assert {
        item.verification_record_id for item in pack.items
    } <= set(registry)
    assert all(record["conclusion"] == "verified" for record in registry.values())


def test_each_component_declares_scope_sign_and_adjustment_controls():
    raw = json.loads(
        (ROOT / "rules/general_enterprise_v1.json").read_text(
            encoding="utf-8-sig"
        )
    )
    for item in raw["items"]:
        for component in item["components"]:
            assert component["source_scope"]
            assert component["sign"] in {-1, 1}
            assert component["occupancy_policy"] in {
                "exclusive",
                "shared_control",
            }
            assert component["gross_or_net"] in {
                "gross",
                "net",
                "not_applicable",
            }
            assert "noncash_exclusions" in component
            assert "special_adjustments" in component
            assert "restricted_cash_treatment" in component


def test_known_third_workbook_formula_defects_are_resolved():
    registry = load_registry(
        ROOT / "references/公式核验/general_enterprise_v1.jsonl"
    )
    sales = registry["FV-G-CFO-01"]
    assert any(
        issue["type"] == "candidate_range_overlap"
        and issue["resolution"] == "收入及销项税加至第7项，票据贴现利息自第8项起作为减项"
        for issue in sales["issues"]
    )
    capital = registry["FV-G-CFF-01"]
    assert any(
        issue["type"] == "single_entity_scope"
        for issue in capital["issues"]
    )


def test_general_rules_make_special_adjustments_computational():
    raw = json.loads(
        (ROOT / "rules/general_enterprise_v1.json").read_text(encoding="utf-8-sig")
    )
    by_id = {item["item_id"]: item for item in raw["items"]}
    sales_tags = {
        tag
        for component in by_id["CFO-01"]["components"]
        for tag in component["selector"].get("tags_any", [])
    }
    assert "receivable_write_off" in sales_tags
    assert "bad_debt_accrual" not in sales_tags
    assert by_id["CFO-01"]["components"][0]["selector"]["item_name_groups"] == [
        ["营业收入"],
        ["主营业务收入", "其他业务收入"],
    ]
    capital_tags = {
        tag
        for component in by_id["CFF-01"]["components"]
        for tag in component["selector"].get("tags_any", [])
    }
    other_financing_tags = {
        tag
        for component in by_id["CFF-06"]["components"]
        for tag in component["selector"].get("tags_any", [])
    }
    borrowing_tags = {
        tag
        for component in by_id["CFF-02"]["components"]
        for tag in component["selector"].get("tags_any", [])
    }
    assert "equity_issue_cost_cash_paid" in capital_tags
    assert "financing_issue_cost_cash_paid" not in other_financing_tags
    assert "bond_issue_cost_cash_paid" in borrowing_tags
    assert "other_financing_cash_paid" in other_financing_tags
    assert any(
        "business_disposal_cost_cash_paid"
        in component["selector"].get("negative_tags_any", [])
        for component in by_id["CFI-04"]["components"]
    )


def test_noncash_long_lived_asset_purchase_does_not_create_negative_cash_outflow():
    registry = load_registry(ROOT / "references/公式核验/general_enterprise_v1.jsonl")
    pack = load_rule_pack(
        ROOT / "rules/general_enterprise_v1.json",
        verification_ids=set(registry),
    )
    facts = FactLedger.index((
        Fact(
            "JP:1",
            10_000,
            ("journal_pair", "noncash_long_lived_asset_addition"),
            ("journal_pair:1",),
            "JP:1:noncash",
        ),
        Fact(
            "TB:2241:closing_change",
            10_000,
            ("trial_balance", "closing_change"),
            ("trial_balance:2241",),
            "TB:2241:closing_change",
            (("account_name", "应付工程款"), ("kind", "closing_change")),
        ),
    ))

    calculation = calculate_items(pack, facts)

    assert calculation.by_id["CFI-06"].amount_minor == 0


def test_negative_disposal_and_acquisition_nets_are_redirected_once():
    registry = load_registry(ROOT / "references/公式核验/general_enterprise_v1.jsonl")
    pack = load_rule_pack(
        ROOT / "rules/general_enterprise_v1.json",
        verification_ids=set(registry),
    )
    facts = FactLedger.index((
        Fact("DISPOSAL-IN", 100, ("long_lived_asset_disposal_cash",), ("src:1",), "K1"),
        Fact("DISPOSAL-OUT", 130, ("long_lived_asset_disposal_cost_cash",), ("src:2",), "K2"),
        Fact("ACQUIRE-OUT", 100, ("business_acquisition_cash_paid",), ("src:3",), "K3"),
        Fact("ACQUIRE-CASH", 140, ("acquired_business_cash_and_equivalents",), ("src:4",), "K4"),
    ))

    calculation = calculate_items(pack, facts)

    assert calculation.by_id["CFI-03"].amount_minor == 0
    assert calculation.by_id["CFI-08"].amount_minor == 0
    assert calculation.by_id["CFI-05"].amount_minor == 40
    assert calculation.by_id["CFI-09"].amount_minor == 30
    assert calculation.allocated == {
        "K1": ("CFI-09-C02",),
        "K2": ("CFI-09-C02",),
        "K3": ("CFI-05-C02",),
        "K4": ("CFI-05-C02",),
    }


def test_exactly_offsetting_disposal_net_is_claimed_by_primary_item():
    registry = load_registry(ROOT / "references/公式核验/general_enterprise_v1.jsonl")
    pack = load_rule_pack(
        ROOT / "rules/general_enterprise_v1.json",
        verification_ids=set(registry),
    )
    facts = FactLedger.index((
        Fact("DISPOSAL-IN", 100, ("long_lived_asset_disposal_cash",), ("src:1",), "K1"),
        Fact("DISPOSAL-OUT", 100, ("long_lived_asset_disposal_cost_cash",), ("src:2",), "K2"),
    ))

    calculation = calculate_items(pack, facts)

    assert calculation.by_id["CFI-03"].amount_minor == 0
    assert calculation.by_id["CFI-09"].amount_minor == 0
    assert calculation.allocated == {
        "K1": ("CFI-03-C01",),
        "K2": ("CFI-03-C01",),
    }
