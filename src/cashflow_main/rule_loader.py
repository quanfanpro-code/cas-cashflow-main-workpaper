"""版本化现金流规则包加载和门禁。"""

import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .contracts import EnterpriseType

ALLOWED_OPERATIONS = {
    "statement_value",
    "balance_change",
    "debit_turnover",
    "credit_turnover",
    "paired_turnover",
    "adjustment_amount",
    "fact_amount",
    "net_fact_amount",
    "subtotal",
    "cash_equivalent_balance",
}
DEFAULT_SCHEMA_PATH = Path(__file__).parents[2] / "rules" / "schema.json"


@dataclass(frozen=True)
class RuleComponent:
    component_id: str
    operation: str
    sign: int
    source_scope: str
    selector: dict[str, object]
    occupancy_policy: str
    gross_or_net: str = "not_applicable"
    noncash_exclusions: tuple[str, ...] = ()
    special_adjustments: tuple[str, ...] = ()
    restricted_cash_treatment: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleItem:
    item_id: str
    name: str
    section: str
    display_order: int
    verification_record_id: str
    components: tuple[RuleComponent, ...]
    is_subtotal: bool = False
    subtotal_of: tuple[str, ...] = ()


@dataclass(frozen=True)
class RulePack:
    version: str
    enterprise_type: EnterpriseType
    statement_template: tuple[dict[str, object], ...]
    account_groups: dict[str, object]
    items: tuple[RuleItem, ...]


def _component(raw: dict[str, object]) -> RuleComponent:
    return RuleComponent(
        component_id=str(raw["component_id"]),
        operation=str(raw["operation"]),
        sign=int(raw["sign"]),
        source_scope=str(raw["source_scope"]),
        selector=dict(raw["selector"]),
        occupancy_policy=str(raw["occupancy_policy"]),
        gross_or_net=str(raw.get("gross_or_net", "not_applicable")),
        noncash_exclusions=tuple(str(value) for value in raw.get("noncash_exclusions", [])),
        special_adjustments=tuple(str(value) for value in raw.get("special_adjustments", [])),
        restricted_cash_treatment=tuple(str(value) for value in raw.get("restricted_cash_treatment", [])),
    )


def _item(raw: dict[str, object]) -> RuleItem:
    return RuleItem(
        item_id=str(raw["item_id"]),
        name=str(raw["name"]),
        section=str(raw["section"]),
        display_order=int(raw["display_order"]),
        verification_record_id=str(raw["verification_record_id"]),
        components=tuple(_component(item) for item in raw["components"]),
        is_subtotal=bool(raw.get("is_subtotal", False)),
        subtotal_of=tuple(str(item) for item in raw.get("subtotal_of", [])),
    )


def load_rule_pack(
    path: Path,
    verification_ids: set[str] | dict[str, dict[str, object]],
    schema_path: Path = DEFAULT_SCHEMA_PATH,
) -> RulePack:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
    try:
        Draft202012Validator(schema).validate(raw)
    except ValidationError as exc:
        raise ValueError(f"规则结构无效：{exc.message}") from exc

    item_ids = [str(item["item_id"]) for item in raw["items"]]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("现金流项目编号重复")
    template_ids = [str(item["item_id"]) for item in raw["statement_template"]]
    if len(template_ids) != len(set(template_ids)):
        raise ValueError("正表模板项目编号重复")
    if set(template_ids) != set(item_ids):
        raise ValueError("正表模板与计算项目不一致")
    template_by_id = {
        str(item["item_id"]): item for item in raw["statement_template"]
    }
    for item in raw["items"]:
        template = template_by_id[str(item["item_id"])]
        if any(
            template.get(field) != item.get(field)
            for field in ("name", "section", "display_order")
        ):
            raise ValueError(f"正表模板与计算项目属性不一致：{item['item_id']}")

    for item in raw["items"]:
        verification_id = item["verification_record_id"]
        if verification_id not in verification_ids:
            raise ValueError(f"未核验公式：{item['item_id']}")
        if isinstance(verification_ids, dict):
            record = verification_ids[verification_id]
            open_issues = [
                issue for issue in record.get("issues", [])
                if not str(issue.get("resolution", "")).strip()
            ]
            if record.get("conclusion") != "verified" or open_issues:
                raise ValueError(f"公式核验未完成：{item['item_id']}:{verification_id}")
        for component in item["components"]:
            if component["operation"] not in ALLOWED_OPERATIONS:
                raise ValueError(f"不允许的操作：{component['operation']}")
            if component["operation"] == "net_fact_amount":
                selector = component.get("selector", {})
                if not selector.get("positive_tags_any") or not selector.get("negative_tags_any"):
                    raise ValueError(
                        f"净额事实操作必须同时声明正向和负向标签：{component['component_id']}"
                    )

    return RulePack(
        version=str(raw["version"]),
        enterprise_type=EnterpriseType(raw["enterprise_type"]),
        statement_template=tuple(raw["statement_template"]),
        account_groups=dict(raw["account_groups"]),
        items=tuple(_item(item) for item in raw["items"]),
    )
