"""把报表、余额表、一借一贷明细和调整桥统一为可追溯事实账。"""

from collections import Counter
from dataclasses import dataclass

from .adjustment_bridge import AdjustmentBridgeResult
from .contracts import EnterpriseType, NormalizedInputBundle
from .semantic_facts import classify_pair


@dataclass(frozen=True)
class Fact:
    fact_id: str
    amount_minor: int
    tags: tuple[str, ...]
    source_ids: tuple[str, ...]
    occupancy_key: str
    attributes: tuple[tuple[str, object], ...] = ()

    @property
    def metadata(self) -> dict[str, object]:
        return dict(self.attributes)


@dataclass
class FactLedger:
    by_id: dict[str, Fact]

    @classmethod
    def index(cls, facts) -> "FactLedger":
        materialized = tuple(facts)
        indexed = {fact.fact_id: fact for fact in materialized}
        if len(indexed) != len(materialized):
            raise ValueError("事实编号重复")
        return cls(indexed)

    def values(self) -> tuple[Fact, ...]:
        return tuple(self.by_id.values())


RESTRICTED_CASH_TERMS = (
    "冻结",
    "质押",
    "保证金",
    "监管",
    "受限",
    "不可随时支取",
)


def _account_evidence(account_name: str, original_fields: dict[str, object]) -> str:
    return " ".join(
        [account_name]
        + [f"{key}:{value}" for key, value in original_fields.items() if value not in (None, "")]
    )


def cash_and_equivalent_control(
    facts: FactLedger,
) -> tuple[int, int, tuple[Fact, ...]]:
    """按同一事实口径返回期初、期末现金及明确受限明细。"""
    cash_facts = tuple(
        fact for fact in facts.values()
        if "cash_equivalent" in fact.tags
        and "restricted_cash" not in fact.tags
    )
    opening = sum(
        fact.amount_minor for fact in cash_facts
        if fact.metadata.get("kind") == "opening"
    )
    closing = sum(
        fact.amount_minor for fact in cash_facts
        if fact.metadata.get("kind") == "closing"
    )
    restricted = tuple(
        fact for fact in facts.values()
        if "restricted_cash" in fact.tags
        and fact.metadata.get("kind") in {"opening", "closing"}
    )
    return opening, closing, restricted


def extract_facts(
    bundle: NormalizedInputBundle,
    bridge: AdjustmentBridgeResult,
    enterprise_type: EnterpriseType,
    account_groups: dict[str, object],
) -> FactLedger:
    facts: list[Fact] = []
    for scope, lines in (
        ("BS", bundle.audited_balance_sheet),
        ("IS", bundle.audited_income_statement),
        ("PRIORCF", bundle.prior_cashflow),
    ):
        for index, line in enumerate(lines, 1):
            fact_id = f"{scope}:{index}:{line.item_name}"
            facts.append(Fact(
                fact_id,
                line.current_minor,
                ("statement", scope.lower()),
                (f"statement:{scope}:{line.item_name}",),
                fact_id,
                (("item_name", line.item_name), ("period", "current")),
            ))
            if line.prior_minor is not None:
                prior_id = f"{scope}:{index}:{line.item_name}:prior"
                facts.append(Fact(
                    prior_id,
                    line.prior_minor,
                    ("statement", scope.lower()),
                    (f"statement:{scope}:{line.item_name}:prior",),
                    prior_id,
                    (("item_name", line.item_name), ("period", "prior")),
                ))
    trial_balance_identities = tuple(
        row.account_code or row.account_name or "NO_ACCOUNT"
        for row in bundle.trial_balance
    )
    identity_counts = Counter(trial_balance_identities)
    cash_names = tuple(str(value) for value in account_groups.get("cash_and_equivalents", ()))
    for row_index, row in enumerate(bundle.trial_balance, 1):
        account_identity = row.account_code or row.account_name or "NO_ACCOUNT"
        identity_is_unique = identity_counts[account_identity] == 1
        source_locator = (
            f"trial_balance:{row.account_code}"
            if row.account_code and identity_is_unique
            else f"trial_balance:{account_identity}:row:{row_index}"
        )
        source = (source_locator,)
        base = (("account_code", row.account_code), ("account_name", row.account_name))
        evidence = _account_evidence(row.account_name, row.original_fields)
        is_cash_equivalent = any(name in evidence for name in cash_names)
        is_restricted = is_cash_equivalent and any(
            term in evidence for term in RESTRICTED_CASH_TERMS
        )
        is_uncertain_other_monetary_funds = (
            "".join(row.account_name.split()) == "其他货币资金"
            and is_cash_equivalent
            and not is_restricted
        )
        values = {
            "opening": row.opening_balance_minor,
            "debit_turnover": row.debit_turnover_minor,
            "credit_turnover": row.credit_turnover_minor,
            "closing": row.closing_balance_minor,
            "closing_change": row.closing_balance_minor - row.opening_balance_minor,
        }
        for kind, amount in values.items():
            fact_id = (
                f"TB:{row.account_code}:{kind}"
                if row.account_code and identity_is_unique
                else f"TB:{account_identity}:{row_index}:{kind}"
            )
            tags = ["trial_balance", kind]
            if is_cash_equivalent:
                tags.append("cash_equivalent")
            if is_restricted:
                tags.append("restricted_cash")
            if is_uncertain_other_monetary_funds:
                tags.append("restricted_cash_uncertain")
            facts.append(Fact(
                fact_id,
                amount,
                tuple(tags),
                source,
                fact_id,
                base + (("kind", kind), ("account_evidence", evidence)),
            ))
    for index, pair in enumerate(bundle.journal_pairs, 1):
        fact_id = f"JP:{index}"
        classification = classify_pair(pair, enterprise_type)
        tags = ["journal_pair", *classification.tags]
        facts.append(Fact(
            fact_id,
            pair.amount_minor,
            tuple(x.strip() for x in tags if x.strip()),
            (f"journal_pair:{index}",),
            fact_id,
            (
                ("debit_account_name", pair.debit_account_name),
                ("credit_account_name", pair.credit_account_name),
                ("classification_evidence", classification.evidence),
                ("supplied_tags", classification.supplied_tags),
                ("tag_conflicts", classification.conflicts),
            ),
        ))
    for row in bridge.rows:
        for adjustment in row.matched_adjustments:
            tags = ["adjustment", adjustment.adjustment_type]
            if adjustment.nature:
                tags.append(adjustment.nature)
            facts.append(Fact(
                adjustment.adjustment_id,
                adjustment.amount_minor,
                tuple(tags),
                adjustment.source_ids or (f"adjustment_bridge:{row.report_item}",),
                adjustment.adjustment_id,
                (("report_item", row.report_item),),
            ))
        if row.unexplained_minor:
            fact_id = f"UNEXPLAINED:{row.report_item}"
            facts.append(Fact(fact_id, row.unexplained_minor, ("adjustment", "unexplained"), (f"adjustment_bridge:{row.report_item}",), fact_id, (("report_item", row.report_item),)))
    return FactLedger.index(tuple(facts))
