import pytest

from cashflow_main.fact_extraction import Fact, FactLedger
from cashflow_main.item_calculators import AllocationError, calculate_items
from cashflow_main.rule_loader import RuleComponent, RuleItem, RulePack
from cashflow_main.contracts import EnterpriseType


def pack(selector=None, *, occupancy="exclusive"):
    component = RuleComponent("X-C1", "fact_amount", 1, "fact_ledger", selector or {"tags_any": ["x"]}, occupancy, "gross")
    item = RuleItem("X", "项目X", "operating", 1, "V-X", (component,))
    return RulePack("1", EnterpriseType.GENERAL, (), {}, (item,))


def test_item_is_sum_of_named_components_and_is_traceable():
    facts = FactLedger.index((Fact("F1", 100, ("x",), ("src:1",), "K1"), Fact("F2", 20, ("x",), ("src:2",), "K2")))
    result = calculate_items(pack(), facts)
    assert result.by_id["X"].amount_minor == 120
    assert result.by_id["X"].components[0].source_ids == ("src:1", "src:2")


def test_exclusive_fact_cannot_be_allocated_twice():
    facts = FactLedger.index((Fact("F1", 100, ("x",), ("src:1",), "K1"),))
    component = RuleComponent("Y-C1", "fact_amount", 1, "fact_ledger", {"tags_any": ["x"]}, "exclusive", "gross")
    p = pack()
    p = RulePack(p.version, p.enterprise_type, (), {}, p.items + (RuleItem("Y", "项目Y", "operating", 2, "V-Y", (component,)),))
    with pytest.raises(AllocationError, match="重复占用"):
        calculate_items(p, facts)


def test_subtotal_uses_prior_calculated_items():
    facts = FactLedger.index((Fact("F1", 100, ("x",), ("src:1",), "K1"),))
    detail = pack().items[0]
    subtotal_component = RuleComponent("T-C1", "subtotal", 1, "calculated_items", {"item_ids": ["X"]}, "shared_control")
    total = RuleItem("T", "小计", "operating", 2, "V-T", (subtotal_component,), True, ("X",))
    p = RulePack("1", EnterpriseType.GENERAL, (), {}, (detail, total))
    result = calculate_items(p, facts)
    assert result.by_id["T"].amount_minor == 100
    details = result.by_id["T"].components[0].fact_details
    assert [(row.fact_label, row.raw_amount_minor, row.applied_amount_minor) for row in details] == [
        ("项目X", 100, 100),
    ]


def test_component_keeps_each_fact_amount_label_and_source_for_visible_review():
    facts = FactLedger.index((
        Fact("F1", 100, ("x",), ("statement:营业收入",), "K1", (("item_name", "营业收入"),)),
        Fact("F2", 20, ("x",), ("trial_balance:1122",), "K2", (("account_name", "应收账款"),)),
    ))
    component = RuleComponent(
        "X-C1", "fact_amount", -1, "fact_ledger", {"tags_any": ["x"]}, "exclusive", "gross"
    )
    item = RuleItem("X", "项目X", "operating", 1, "V-X", (component,))
    result = calculate_items(
        RulePack("1", EnterpriseType.GENERAL, (), {}, (item,)), facts
    )

    details = result.by_id["X"].components[0].fact_details
    assert [(row.fact_label, row.raw_amount_minor, row.applied_amount_minor) for row in details] == [
        ("营业收入", 100, -100),
        ("应收账款", 20, -20),
    ]
    assert details[0].source_ids == ("statement:营业收入",)
    assert sum(row.applied_amount_minor for row in details) == result.by_id["X"].components[0].amount_minor


def test_component_keeps_classification_evidence_and_label_conflict_for_visible_review():
    facts = FactLedger.index((Fact(
        "JP:1", 100, ("x",), ("journal_pair:1",), "K1",
        (
            ("classification_evidence", ("借记现金、贷记短期借款",)),
            ("supplied_tags", ("investment_acquisition_cash",)),
            ("tag_conflicts", ("investment_acquisition_cash",)),
        ),
    ),))

    detail = calculate_items(pack(), facts).by_id["X"].components[0].fact_details[0]

    assert detail.classification_evidence == ("借记现金、贷记短期借款",)
    assert detail.supplied_tags == ("investment_acquisition_cash",)
    assert detail.tag_conflicts == ("investment_acquisition_cash",)


def test_positive_net_fact_amount_splits_received_and_paid_without_negative_inflow():
    facts = FactLedger.index((
        Fact("R", 40, ("received",), ("jp:r",), "R"),
        Fact("P", 100, ("paid",), ("jp:p",), "P"),
    ))
    received = RuleComponent(
        "IN-C1", "net_fact_amount", 1, "fact_ledger",
        {
            "positive_tags_any": ["received"],
            "negative_tags_any": ["paid"],
            "positive_only": True,
        },
        "exclusive", "net",
    )
    paid = RuleComponent(
        "OUT-C1", "net_fact_amount", 1, "fact_ledger",
        {
            "positive_tags_any": ["paid"],
            "negative_tags_any": ["received"],
            "positive_only": True,
        },
        "exclusive", "net",
    )
    rule_pack = RulePack("1", EnterpriseType.INSURANCE, (), {}, (
        RuleItem("IN", "收到净额", "operating", 1, "V", (received,)),
        RuleItem("OUT", "支付净额", "operating", 2, "V", (paid,)),
    ))

    result = calculate_items(rule_pack, facts)

    assert result.by_id["IN"].amount_minor == 0
    assert result.by_id["OUT"].amount_minor == 60
    assert result.allocated == {"R": ("OUT-C1",), "P": ("OUT-C1",)}
    assert [row.applied_amount_minor for row in result.by_id["IN"].components[0].fact_details] == [0, 0]


def test_no_hit_balance_component_keeps_human_readable_account_label():
    component = RuleComponent(
        "X-C1", "balance_change", 1, "trial_balance",
        {"account_groups": ["receivables"], "direction": "opening_minus_closing"},
        "exclusive", "gross",
    )
    item = RuleItem("X", "项目X", "operating", 1, "V-X", (component,))
    result = calculate_items(
        RulePack("1", EnterpriseType.GENERAL, (), {"receivables": ["应收账款", "应收票据"]}, (item,)),
        FactLedger.index(()),
    )
    assert result.by_id["X"].components[0].selector_label == "应收账款、应收票据（期初－期末）"


def test_cash_equivalent_balance_selector_excludes_restricted_facts():
    component = RuleComponent(
        "CASH-C1", "cash_equivalent_balance", 1, "trial_balance",
        {"period": "closing", "exclude_restricted": True},
        "shared_control", "not_applicable",
    )
    item = RuleItem("CASH", "现金余额", "cash", 1, "V-CASH", (component,))
    facts = FactLedger.index((
        Fact("C1", 100, ("trial_balance", "closing", "cash_equivalent"), ("tb:1",), "C1", (("kind", "closing"), ("account_name", "银行存款"))),
        Fact("C2", 20, ("trial_balance", "closing", "cash_equivalent", "restricted_cash"), ("tb:2",), "C2", (("kind", "closing"), ("account_name", "冻结存款"))),
    ))
    result = calculate_items(
        RulePack("1", EnterpriseType.GENERAL, (), {"cash_and_equivalents": ["银行存款", "冻结存款"]}, (item,)),
        facts,
    )
    assert result.by_id["CASH"].amount_minor == 100


def test_statement_value_uses_total_or_details_without_double_counting():
    component = RuleComponent(
        "REV-C1", "statement_value", 1, "audited_statements",
        {
            "item_name_groups": [["营业收入"], ["主营业务收入", "其他业务收入"]],
            "period": "current",
        },
        "exclusive", "gross",
    )
    item = RuleItem("REV", "收入", "operating", 1, "V-REV", (component,))
    facts = FactLedger.index((
        Fact("T", 100, ("statement",), ("is:1",), "T", (("item_name", "营业收入"), ("period", "current"))),
        Fact("M", 80, ("statement",), ("is:2",), "M", (("item_name", "主营业务收入"), ("period", "current"))),
        Fact("O", 20, ("statement",), ("is:3",), "O", (("item_name", "其他业务收入"), ("period", "current"))),
    ))
    assert calculate_items(
        RulePack("1", EnterpriseType.GENERAL, (), {}, (item,)), facts
    ).by_id["REV"].amount_minor == 100


def test_subtotal_rejects_missing_item_and_mismatched_signs():
    facts = FactLedger.index((Fact("F1", 100, ("x",), ("src",), "K"),))
    detail = pack().items[0]
    missing = RuleComponent("T-C1", "subtotal", 1, "calculated_items", {"item_ids": ["X", "MISSING"]}, "shared_control")
    with pytest.raises(AllocationError, match="引用不存在"):
        calculate_items(RulePack("1", EnterpriseType.GENERAL, (), {}, (detail, RuleItem("T", "小计", "operating", 2, "V-T", (missing,)))), facts)

    bad_signs = RuleComponent("T-C2", "subtotal", 1, "calculated_items", {"item_ids": ["X"], "signs": [1, -1]}, "shared_control")
    with pytest.raises(AllocationError, match="数量不一致"):
        calculate_items(RulePack("1", EnterpriseType.GENERAL, (), {}, (detail, RuleItem("T", "小计", "operating", 2, "V-T", (bad_signs,)))), facts)
