"""以内存分配矩阵检查漏项、重复、小计和现金变动。"""

from dataclasses import dataclass

from .fact_extraction import Fact, FactLedger
from .item_calculators import CalculationResult
from .rule_loader import RulePack


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    fact_id: str | None = None
    amount_minor: int = 0


@dataclass(frozen=True)
class ValidationReport:
    unallocated: tuple[ValidationIssue, ...]
    duplicate_allocations: tuple[ValidationIssue, ...]
    subtotal_errors: tuple[ValidationIssue, ...]
    cash_change_difference_minor: int
    human_review_cases: tuple[ValidationIssue, ...] = ()

    @property
    def is_blocking(self) -> bool:
        return bool(self.unallocated or self.duplicate_allocations or self.subtotal_errors or self.cash_change_difference_minor)

    @property
    def is_clean(self) -> bool:
        return not self.is_blocking


def validate_completeness(
    facts: FactLedger,
    calculation: CalculationResult,
    rule_pack: RulePack,
    cash_opening_minor: int,
    cash_closing_minor: int,
) -> ValidationReport:
    controlled_tags = {
        str(tag)
        for item in rule_pack.items
        for component in item.components
        for key in ("tags_any", "positive_tags_any", "negative_tags_any")
        for tag in component.selector.get(key, ())
    }
    unallocated = tuple(
        ValidationIssue("unallocated", "控制事实未分配", fact.fact_id, fact.amount_minor)
        for fact in facts.values()
        if ("control" in fact.tags or controlled_tags.intersection(fact.tags))
        and fact.amount_minor
        and fact.occupancy_key not in calculation.allocated
    )
    duplicates = tuple(
        ValidationIssue("duplicate_allocation", f"控制事实重复分配至{','.join(components)}", key)
        for key, components in calculation.allocated.items()
        if len(components) > 1
    )
    by_id = calculation.by_id
    subtotal_errors = []
    activity = (("CFO-IN", "CFO-OUT", "CFO-NET"), ("CFI-IN", "CFI-OUT", "CFI-NET"), ("CFF-IN", "CFF-OUT", "CFF-NET"))
    for inflow, outflow, net in activity:
        if all(key in by_id for key in (inflow, outflow, net)):
            difference = by_id[net].amount_minor - (by_id[inflow].amount_minor - by_id[outflow].amount_minor)
            if difference:
                subtotal_errors.append(ValidationIssue("subtotal", f"{net}与流入流出小计不一致", net, difference))
    calculated_change = by_id.get("NET-CASH") or by_id.get("CF-NET")
    actual_change = cash_closing_minor - cash_opening_minor
    cash_difference = (calculated_change.amount_minor if calculated_change else 0) - actual_change
    return ValidationReport(unallocated, duplicates, tuple(subtotal_errors), cash_difference)
