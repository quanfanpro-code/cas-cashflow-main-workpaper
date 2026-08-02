"""按已核验规则逐组成计算现金流量表项目，不执行任意表达式。"""

from dataclasses import dataclass, field

from .fact_extraction import Fact, FactLedger
from .rule_loader import RuleComponent, RulePack


class AllocationError(ValueError):
    pass


@dataclass(frozen=True)
class ComponentFactResult:
    fact_id: str
    fact_label: str
    raw_amount_minor: int
    applied_amount_minor: int
    source_ids: tuple[str, ...]
    occupancy_key: str
    classification_evidence: tuple[str, ...] = ()
    supplied_tags: tuple[str, ...] = ()
    tag_conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ItemComponentResult:
    rule_component_id: str
    amount_minor: int
    fact_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    occupancy_keys: tuple[str, ...]
    fact_details: tuple[ComponentFactResult, ...] = ()
    operation: str = ""
    sign: int = 1
    source_scope: str = ""
    selector: dict[str, object] = field(default_factory=dict)
    gross_or_net: str = "not_applicable"
    noncash_exclusions: tuple[str, ...] = ()
    special_adjustments: tuple[str, ...] = ()
    restricted_cash_treatment: tuple[str, ...] = ()
    selector_label: str = ""


@dataclass(frozen=True)
class CashflowItemResult:
    item_id: str
    name: str
    section: str
    display_order: int
    amount_minor: int
    components: tuple[ItemComponentResult, ...]
    verification_record_id: str = ""


@dataclass(frozen=True)
class CalculationResult:
    items: tuple[CashflowItemResult, ...]
    allocated: dict[str, tuple[str, ...]]
    enterprise_type: object | None = None

    @property
    def by_id(self) -> dict[str, CashflowItemResult]:
        return {item.item_id: item for item in self.items}


def _matches(
    fact: Fact,
    selector: dict[str, object],
    account_groups: dict[str, object],
) -> bool:
    metadata = fact.metadata
    tags = set(fact.tags)
    if selector.get("tags_any") and not tags.intersection(selector["tags_any"]):
        return False
    if selector.get("exclude_tags_any") and tags.intersection(selector["exclude_tags_any"]):
        return False
    if selector.get("item_names") and metadata.get("item_name") not in selector["item_names"]:
        return False
    if selector.get("period") and metadata.get("period", metadata.get("kind")) != selector["period"]:
        return False
    if selector.get("account_names") and metadata.get("account_name") not in selector["account_names"]:
        return False
    if selector.get("account_groups"):
        allowed_names = {
            name
            for group in selector["account_groups"]
            for name in account_groups.get(group, [])
        }
        if not any(name in str(metadata.get("account_name", "")) for name in allowed_names):
            return False
    return True


def _select(
    facts: FactLedger,
    component: RuleComponent,
    account_groups: dict[str, object],
) -> tuple[Fact, ...]:
    selector = component.selector
    operation = component.operation
    if operation == "statement_value":
        candidates = [f for f in facts.values() if "statement" in f.tags]
    elif operation == "balance_change":
        candidates = [f for f in facts.values() if "closing_change" in f.tags]
    elif operation == "debit_turnover":
        candidates = [f for f in facts.values() if "debit_turnover" in f.tags]
    elif operation == "credit_turnover":
        candidates = [f for f in facts.values() if "credit_turnover" in f.tags]
    elif operation == "paired_turnover":
        candidates = [f for f in facts.values() if "journal_pair" in f.tags]
    elif operation == "adjustment_amount":
        candidates = [f for f in facts.values() if "adjustment" in f.tags]
    elif operation == "cash_equivalent_balance":
        period_kind = {"opening": "opening", "closing": "closing"}.get(str(selector.get("period")))
        cash_names = account_groups.get("cash_and_equivalents", [])
        return tuple(
            f
            for f in facts.values()
            if f.metadata.get("kind") == period_kind
            and (
                "cash_equivalent" in f.tags
                or any(name in str(f.metadata.get("account_name", "")) for name in cash_names)
            )
            and not (
                selector.get("exclude_restricted", True)
                and "restricted_cash" in f.tags
            )
            and _matches(f, selector, account_groups)
        )
    elif operation in {"fact_amount", "net_fact_amount"}:
        candidates = list(facts.values())
    else:
        raise AllocationError(f"不支持的选择操作：{operation}")
    matched = tuple(f for f in candidates if _matches(f, selector, account_groups))
    if operation == "statement_value" and selector.get("item_name_groups"):
        for group in selector["item_name_groups"]:
            selected_group = tuple(
                fact for fact in matched
                if fact.metadata.get("item_name") in group
            )
            if selected_group:
                return selected_group
        return ()
    return matched


def _selector_label(item_name: str, component: RuleComponent, account_groups: dict[str, object]) -> str:
    selector = component.selector
    if selector.get("item_names"):
        return "、".join(str(value) for value in selector["item_names"])
    if selector.get("account_groups"):
        names = tuple(dict.fromkeys(
            str(name)
            for group in selector["account_groups"]
            for name in account_groups.get(group, [])
        ))
        label = "、".join(names) or "相关科目"
        direction = {
            "opening_minus_closing": "期初－期末",
            "closing_minus_opening": "期末－期初",
        }.get(str(selector.get("direction")))
        return f"{label}（{direction}）" if direction else label
    if selector.get("account_names"):
        return "、".join(str(value) for value in selector["account_names"])
    if component.operation == "cash_equivalent_balance":
        return "期初现金及现金等价物余额" if selector.get("period") == "opening" else "期末现金及现金等价物余额"
    if selector.get("positive_tags_any") or selector.get("negative_tags_any"):
        positive = "、".join(str(value) for value in selector.get("positive_tags_any", ()))
        negative = "、".join(str(value) for value in selector.get("negative_tags_any", ()))
        return f"{item_name}净额（正向：{positive or '无'}；负向：{negative or '无'}）"
    if selector.get("tags_any"):
        tags = "、".join(str(value) for value in selector["tags_any"])
        return f"{item_name}相关补充事实（{tags}）"
    return f"{item_name}相关金额"


def _metadata_tuple(fact: Fact, key: str) -> tuple[str, ...]:
    value = fact.metadata.get(key, ())
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if item not in (None, ""))
    return (str(value),)


def _fact_result(fact: Fact, applied_amount_minor: int) -> ComponentFactResult:
    return ComponentFactResult(
        fact_id=fact.fact_id,
        fact_label=str(
            fact.metadata.get("item_name")
            or fact.metadata.get("account_name")
            or fact.metadata.get("report_item")
            or fact.fact_id
        ),
        raw_amount_minor=fact.amount_minor,
        applied_amount_minor=applied_amount_minor,
        source_ids=fact.source_ids,
        occupancy_key=fact.occupancy_key,
        classification_evidence=_metadata_tuple(fact, "classification_evidence"),
        supplied_tags=_metadata_tuple(fact, "supplied_tags"),
        tag_conflicts=_metadata_tuple(fact, "tag_conflicts"),
    )


def calculate_items(rule_pack: RulePack, facts: FactLedger) -> CalculationResult:
    results: list[CashflowItemResult] = []
    allocated: dict[str, list[str]] = {}
    for item in sorted(rule_pack.items, key=lambda value: value.display_order):
        components: list[ItemComponentResult] = []
        for component in item.components:
            if component.operation == "subtotal":
                selector = component.selector
                by_id = {result.item_id: result for result in results}
                item_ids = selector.get("item_ids", [])
                signs = selector.get("signs", [1] * len(item_ids))
                if len(signs) != len(item_ids):
                    raise AllocationError(
                        f"小计{component.component_id}的项目与正负号数量不一致"
                    )
                missing = [item_id for item_id in item_ids if item_id not in by_id]
                missing.extend(
                    item_id
                    for item_id in selector.get("subtract_item_ids", [])
                    if item_id not in by_id
                )
                if missing:
                    raise AllocationError(
                        f"小计{component.component_id}引用不存在或尚未计算的项目：{'、'.join(missing)}"
                    )
                referenced = [
                    (by_id[item_id], int(sign) * component.sign)
                    for item_id, sign in zip(item_ids, signs, strict=True)
                ]
                referenced.extend(
                    (by_id[item_id], -component.sign)
                    for item_id in selector.get("subtract_item_ids", [])
                )
                fact_details = tuple(
                    ComponentFactResult(
                        fact_id=f"calculated:{result.item_id}",
                        fact_label=result.name,
                        raw_amount_minor=result.amount_minor,
                        applied_amount_minor=result.amount_minor * sign,
                        source_ids=(f"cashflow_item:{result.item_id}",),
                        occupancy_key=f"subtotal:{item.item_id}:{component.component_id}:{result.item_id}",
                    )
                    for result, sign in referenced
                )
                amount = sum(value.applied_amount_minor for value in fact_details)
                components.append(ItemComponentResult(
                    component.component_id,
                    amount,
                    tuple(value.fact_id for value in fact_details),
                    tuple(value.source_ids[0] for value in fact_details),
                    tuple(value.occupancy_key for value in fact_details),
                    fact_details,
                    operation=component.operation,
                    sign=component.sign,
                    source_scope=component.source_scope,
                    selector=component.selector,
                    gross_or_net=component.gross_or_net,
                    noncash_exclusions=component.noncash_exclusions,
                    special_adjustments=component.special_adjustments,
                    restricted_cash_treatment=component.restricted_cash_treatment,
                    selector_label="；".join(value.fact_label for value in fact_details),
                ))
                continue
            if component.operation == "net_fact_amount":
                selector = component.selector
                base_selector = {
                    key: value
                    for key, value in selector.items()
                    if key not in {"positive_tags_any", "negative_tags_any", "positive_only"}
                }
                positive_selector = {
                    **base_selector,
                    "tags_any": selector.get("positive_tags_any", ()),
                }
                negative_selector = {
                    **base_selector,
                    "tags_any": selector.get("negative_tags_any", ()),
                }
                positive = tuple(
                    fact for fact in facts.values()
                    if _matches(fact, positive_selector, rule_pack.account_groups)
                )
                negative = tuple(
                    fact for fact in facts.values()
                    if _matches(fact, negative_selector, rule_pack.account_groups)
                )
                raw_net = sum(fact.amount_minor for fact in positive) - sum(
                    fact.amount_minor for fact in negative
                )
                active = not selector.get("positive_only", False) or raw_net > 0
                selected = positive + negative
                allocated_selected = selected if active else ()
                if component.occupancy_policy == "exclusive":
                    repeated = [
                        fact.occupancy_key
                        for fact in allocated_selected
                        if allocated.get(fact.occupancy_key)
                    ]
                    if repeated:
                        raise AllocationError(f"事实重复占用：{','.join(repeated)}")
                fact_details = tuple(
                    _fact_result(
                        fact,
                        fact.amount_minor * component.sign if active else 0,
                    )
                    for fact in positive
                ) + tuple(
                    _fact_result(
                        fact,
                        -fact.amount_minor * component.sign if active else 0,
                    )
                    for fact in negative
                )
                amount = sum(value.applied_amount_minor for value in fact_details)
                for fact in allocated_selected:
                    allocated.setdefault(fact.occupancy_key, []).append(component.component_id)
                components.append(ItemComponentResult(
                    component.component_id,
                    amount,
                    tuple(fact.fact_id for fact in selected),
                    tuple(dict.fromkeys(source for fact in selected for source in fact.source_ids)),
                    tuple(fact.occupancy_key for fact in selected),
                    fact_details,
                    component.operation,
                    component.sign,
                    component.source_scope,
                    component.selector,
                    component.gross_or_net,
                    component.noncash_exclusions,
                    component.special_adjustments,
                    component.restricted_cash_treatment,
                    _selector_label(item.name, component, rule_pack.account_groups),
                ))
                continue
            selected = _select(facts, component, rule_pack.account_groups)
            if component.occupancy_policy == "exclusive":
                repeated = [f.occupancy_key for f in selected if allocated.get(f.occupancy_key)]
                if repeated:
                    raise AllocationError(f"事实重复占用：{','.join(repeated)}")
            direction = component.selector.get("direction")
            direction_sign = -1 if direction == "opening_minus_closing" else 1
            amount = sum(f.amount_minor for f in selected) * component.sign * direction_sign
            fact_details = tuple(
                _fact_result(
                    fact,
                    fact.amount_minor * component.sign * direction_sign,
                )
                for fact in selected
            )
            for fact in selected:
                allocated.setdefault(fact.occupancy_key, []).append(component.component_id)
            components.append(ItemComponentResult(
                component.component_id,
                amount,
                tuple(f.fact_id for f in selected),
                tuple(dict.fromkeys(source for f in selected for source in f.source_ids)),
                tuple(f.occupancy_key for f in selected),
                fact_details,
                component.operation,
                component.sign,
                component.source_scope,
                component.selector,
                component.gross_or_net,
                component.noncash_exclusions,
                component.special_adjustments,
                component.restricted_cash_treatment,
                _selector_label(item.name, component, rule_pack.account_groups),
            ))
        total = sum(component.amount_minor for component in components)
        results.append(CashflowItemResult(
            item.item_id,
            item.name,
            item.section,
            item.display_order,
            total,
            tuple(components),
            item.verification_record_id,
        ))
    return CalculationResult(
        tuple(results),
        {key: tuple(value) for key, value in allocated.items()},
        rule_pack.enterprise_type,
    )
