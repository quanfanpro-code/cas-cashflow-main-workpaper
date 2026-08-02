"""把正表每个组成追溯到规则、核验记录和源数据。"""

from dataclasses import asdict

from .item_calculators import CalculationResult
from .rule_loader import RulePack


def build_audit_trace(calculation: CalculationResult, rule_pack: RulePack) -> list[dict[str, object]]:
    rules = {item.item_id: item for item in rule_pack.items}
    trace = []
    for item in calculation.items:
        rule = rules[item.item_id]
        for component in item.components:
            row = asdict(component)
            row.update({"item_id": item.item_id, "rule_id": f"{rule_pack.enterprise_type}:{rule_pack.version}:{item.item_id}", "verification_id": rule.verification_record_id})
            trace.append(row)
    return trace

