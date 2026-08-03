"""把报表、余额表、一借一贷明细和调整桥统一为可追溯事实账。"""

from collections import Counter, defaultdict
from dataclasses import dataclass
import re

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

LONG_TERM_DEPOSIT_TERMS = (
    "超过三个月",
    "三个月以上",
    "半年期",
    "六个月",
    "九个月",
    "一年期",
    "二年期",
    "两年期",
    "三年期",
    "五年期",
)

AVAILABLE_DEPOSIT_TERMS = (
    "三个月内",
    "可随时支取",
    "提前通知可支取",
)


def _is_long_term_deposit(evidence: str) -> bool:
    if "定期" not in evidence:
        return False
    if any(term in evidence for term in LONG_TERM_DEPOSIT_TERMS):
        return True
    month = re.search(r"(\d+)\s*个?月", evidence)
    return bool(month and int(month.group(1)) > 3)


def _account_evidence(account_name: str, original_fields: dict[str, object]) -> str:
    return " ".join(
        [account_name]
        + [f"{key}:{value}" for key, value in original_fields.items() if value not in (None, "")]
    )


def _normalized_name(value: str) -> str:
    return re.sub(r"[\s_—－-]+", "", value or "").lower()


def _matches_account_group(
    account_name: str,
    account_groups: dict[str, object],
    group_names: tuple[str, ...],
) -> bool:
    normalized = _normalized_name(account_name)
    return any(
        normalized.startswith(_normalized_name(str(term)))
        for group_name in group_names
        for term in account_groups.get(group_name, ())
        if _normalized_name(str(term))
    )


def _same_account(left: str, right: str) -> bool:
    return _normalized_name(left) == _normalized_name(right)


def _pair_context(pair) -> str:
    return _normalized_name(" ".join(
        str(value) for value in pair.original_fields.values() if value not in (None, "")
    ))


def _capital_liability_analysis(bundle, classifications, account_groups):
    """按负债来源识别资本性付现；普通混合往来只列候选，不强行猜测。"""
    extra: dict[int, list[tuple[str, int, tuple[str, ...]]]] = {}
    residual = {index: pair.amount_minor for index, pair in enumerate(bundle.journal_pairs, 1)}
    ambiguous: dict[int, tuple[int, tuple[str, ...]]] = {}
    cash_groups = ("cash_and_equivalents",)
    credit_indices: dict[str, list[int]] = defaultdict(list)
    cash_payment_indices: dict[str, list[int]] = defaultdict(list)
    account_names: dict[str, str] = {}
    for index, pair in enumerate(bundle.journal_pairs, 1):
        credit_key = _normalized_name(pair.credit_account_name)
        debit_key = _normalized_name(pair.debit_account_name)
        credit_indices[credit_key].append(index)
        account_names.setdefault(credit_key, pair.credit_account_name)
        account_names.setdefault(debit_key, pair.debit_account_name)
        if _matches_account_group(pair.credit_account_name, account_groups, cash_groups):
            cash_payment_indices[debit_key].append(index)
    opening_by_account: dict[str, int] = defaultdict(int)
    for row in bundle.trial_balance:
        opening_by_account[_normalized_name(row.account_name)] += row.opening_balance_minor
    for index, pair in enumerate(bundle.journal_pairs, 1):
        if not (
            _matches_account_group(pair.debit_account_name, account_groups, ("capex_prepayments",))
            and _matches_account_group(pair.credit_account_name, account_groups, cash_groups)
        ):
            continue
        extra.setdefault(index, []).append((
            "long_lived_asset_cash_addition",
            pair.amount_minor,
            ("配置明确该科目为工程或设备预付款，按实际支付额归入长期资产购建现金",),
        ))
        if _matches_account_group(pair.debit_account_name, account_groups, ("trade_prepayments",)):
            extra[index].append((
                "capex_prepayment_change",
                pair.amount_minor,
                ("资本性预付款已混入经营预付款余额变动，予以中和",),
            ))
        residual[index] = 0
    configs = (
        (
            "capex_payable_accrual",
            ("capex_payables",),
            ("trade_payables", "notes_payable"),
            "capex_payable_cash_paid",
            "operating_payable_capex_accrual_adjustment",
            "operating_payable_capex_cash_adjustment",
            ("工程", "设备", "购建", "固定资产", "无形资产", "长期资产"),
        ),
        (
            "capex_employee_accrual",
            ("capex_employee_payables",),
            ("employee_benefits_payable_operating",),
            "capex_employee_cash_paid",
            "operating_employee_capex_accrual_adjustment",
            "operating_employee_capex_cash_adjustment",
            ("工程人员", "开发人员", "资本化", "在建工程", "开发支出"),
        ),
    )

    for (
        accrual_tag,
        explicit_groups,
        operating_groups,
        cash_tag,
        accrual_adjustment_tag,
        cash_adjustment_tag,
        context_terms,
    ) in configs:
        accrual_indices = {
            index
            for index, classification in enumerate(classifications, 1)
            if accrual_tag in classification.tags
        }
        liability_keys = {
            _normalized_name(bundle.journal_pairs[index - 1].credit_account_name)
            for index in accrual_indices
        }
        liability_keys.update(
            account_key
            for account_key in cash_payment_indices
            if _matches_account_group(account_names[account_key], account_groups, explicit_groups)
        )
        liability_keys.update(
            account_key
            for account_key, payment_indices in cash_payment_indices.items()
            if _matches_account_group(account_names[account_key], account_groups, operating_groups)
            and any(
                any(
                    _normalized_name(term) in _pair_context(bundle.journal_pairs[index - 1])
                    for term in context_terms
                )
                for index in payment_indices
            )
        )

        for liability_key in liability_keys:
            liability_name = account_names[liability_key]
            account_accruals = [
                index
                for index in credit_indices[liability_key]
                if index in accrual_indices
            ]
            for index in account_accruals:
                pair = bundle.journal_pairs[index - 1]
                if _matches_account_group(pair.credit_account_name, account_groups, operating_groups):
                    extra.setdefault(index, []).append((
                        accrual_adjustment_tag,
                        pair.amount_minor,
                        ("资本性负债形成金额已混入经营往来余额变动，予以中和",),
                    ))

            payments = cash_payment_indices.get(liability_key, ())
            if not payments:
                continue
            credit_sources = [
                index
                for index in credit_indices[liability_key]
                if not _matches_account_group(
                    bundle.journal_pairs[index - 1].debit_account_name,
                    account_groups,
                    cash_groups,
                )
            ]
            opening = opening_by_account[liability_key]
            available = sum(bundle.journal_pairs[index - 1].amount_minor for index in account_accruals)
            single_origin = bool(credit_sources) and opening == 0 and set(credit_sources) <= accrual_indices
            explicit_account = _matches_account_group(liability_name, account_groups, explicit_groups)

            for index in payments:
                pair = bundle.journal_pairs[index - 1]
                explicit_context = any(term in _pair_context(pair) for term in context_terms)
                if explicit_account or explicit_context:
                    capital_amount = pair.amount_minor
                elif single_origin:
                    capital_amount = min(pair.amount_minor, available)
                else:
                    capital_amount = 0
                if capital_amount:
                    extra.setdefault(index, []).append((
                        cash_tag,
                        capital_amount,
                        ("负债科目及本期来源能够证明该现金结算属于资本性支出",),
                    ))
                    if _matches_account_group(pair.debit_account_name, account_groups, operating_groups):
                        extra[index].append((
                            cash_adjustment_tag,
                            capital_amount,
                            ("资本性负债支付金额已混入经营往来余额变动，予以中和",),
                        ))
                    residual[index] -= capital_amount
                    available = max(0, available - capital_amount)
                unresolved = pair.amount_minor - capital_amount
                if unresolved and account_accruals and not single_origin and not explicit_account and not explicit_context:
                    ambiguous[index] = (
                        unresolved,
                        (
                            "同一普通负债科目同时存在资本性和非资本性来源",
                            "摘要及科目关系不能唯一证明本次付款清偿哪一类负债",
                        ),
                    )
    return extra, residual, ambiguous


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
        if {"restricted_cash", "non_cash_equivalent"}.intersection(fact.tags)
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
        is_term_deposit = "定期存款" in evidence or "定期" in row.account_name
        is_cash_candidate = any(name in evidence for name in cash_names) or is_term_deposit
        is_long_term_deposit = is_cash_candidate and _is_long_term_deposit(evidence)
        is_uncertain_term_deposit = (
            is_term_deposit
            and not is_long_term_deposit
            and not any(term in evidence for term in AVAILABLE_DEPOSIT_TERMS)
        )
        is_restricted = is_cash_candidate and any(
            term in evidence for term in RESTRICTED_CASH_TERMS
        )
        is_cash_equivalent = (
            is_cash_candidate
            and not is_restricted
            and not is_long_term_deposit
            and not is_uncertain_term_deposit
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
            if is_long_term_deposit:
                tags.append("non_cash_equivalent")
                tags.append("long_term_deposit")
            if is_uncertain_term_deposit:
                tags.append("cash_equivalent_uncertain")
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
    classifications = tuple(
        classify_pair(pair, enterprise_type) for pair in bundle.journal_pairs
    )
    capital_facts, residual_amounts, capital_ambiguities = _capital_liability_analysis(
        bundle,
        classifications,
        account_groups,
    )
    for index, (pair, classification) in enumerate(
        zip(bundle.journal_pairs, classifications, strict=True),
        1,
    ):
        semantic_tags = classification.tags or (
            ("unclassified_cash",)
            if classification.is_cash_pair
            else ("unclassified_noncash",)
        )
        residual_amount = residual_amounts[index]
        for tag_index, semantic_tag in enumerate(semantic_tags if residual_amount else ()):
            base_fact_id = f"JP:{index}"
            fact_id = (
                base_fact_id
                if tag_index == 0
                else f"{base_fact_id}:{tag_index + 1}:{semantic_tag}"
            )
            facts.append(Fact(
                fact_id,
                residual_amount,
                ("journal_pair", semantic_tag),
                (f"journal_pair:{index}",),
                f"{base_fact_id}:{semantic_tag}",
                (
                    ("debit_account_name", pair.debit_account_name),
                    ("credit_account_name", pair.credit_account_name),
                    ("classification_evidence", classification.evidence),
                    ("supplied_tags", classification.supplied_tags),
                    ("tag_conflicts", classification.conflicts if tag_index == 0 else ()),
                    ("classification_candidates", classification.candidate_tags if tag_index == 0 else ()),
                    ("classification_preferred", classification.preferred_tag or ""),
                    ("classification_strong_conflict", classification.strong_conflict if tag_index == 0 else False),
                ),
            ))
        for tag, amount, evidence in capital_facts.get(index, ()):
            fact_id = f"JP:{index}:capital:{tag}"
            capital_cash_tags = {
                "long_lived_asset_cash_addition",
                "capex_payable_cash_paid",
                "capex_employee_cash_paid",
            }
            tag_conflicts = (
                tuple(
                    supplied_tag
                    for supplied_tag in classification.supplied_tags
                    if supplied_tag not in capital_cash_tags
                )
                if tag in capital_cash_tags
                else ()
            )
            facts.append(Fact(
                fact_id,
                amount,
                ("journal_pair", tag),
                (f"journal_pair:{index}",),
                f"JP:{index}:{tag}",
                (
                    ("debit_account_name", pair.debit_account_name),
                    ("credit_account_name", pair.credit_account_name),
                    ("classification_evidence", evidence),
                    ("supplied_tags", classification.supplied_tags),
                    ("tag_conflicts", tag_conflicts),
                    ("classification_candidates", ()),
                    ("classification_preferred", tag),
                    ("classification_strong_conflict", False),
                ),
            ))
        if index in capital_ambiguities:
            amount, evidence = capital_ambiguities[index]
            fact_id = f"JP:{index}:capital:ambiguous"
            facts.append(Fact(
                fact_id,
                amount,
                ("journal_pair", "capital_payment_ambiguous"),
                (f"journal_pair:{index}",),
                f"JP:{index}:capital_payment_ambiguous",
                (
                    ("debit_account_name", pair.debit_account_name),
                    ("credit_account_name", pair.credit_account_name),
                    ("classification_evidence", evidence),
                    ("supplied_tags", classification.supplied_tags),
                    ("tag_conflicts", ()),
                    ("classification_candidates", ("CFO-04", "CFI-06")),
                    ("classification_preferred", "CFO-04"),
                    ("classification_strong_conflict", True),
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
