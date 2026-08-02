"""一借一贷明细与科目余额表发生额核对。"""

import re
from collections import defaultdict
from dataclasses import dataclass

from .contracts import AccountBalance, JournalPair


@dataclass(frozen=True)
class LedgerDifference:
    account_name: str
    side: str
    expected_minor: int
    actual_minor: int
    difference_minor: int
    kind: str = "turnover_difference"


@dataclass(frozen=True)
class LedgerReconciliationResult:
    is_reconciled: bool
    differences: tuple[LedgerDifference, ...]


def _journal_totals(
    pairs: tuple[JournalPair, ...] | list[JournalPair],
) -> tuple[dict[str, int], dict[str, int]]:
    debit: dict[str, int] = defaultdict(int)
    credit: dict[str, int] = defaultdict(int)
    for pair in pairs:
        debit[pair.debit_account_name] += pair.amount_minor
        credit[pair.credit_account_name] += pair.amount_minor
    return dict(debit), dict(credit)


def _account_key(value: str) -> str:
    return re.sub(r"[\s_—－\-（）()]+", "", value or "")


def _related_account_names(left: str, right: str) -> bool:
    left_key = _account_key(left)
    right_key = _account_key(right)
    return bool(left_key and right_key and (left_key in right_key or right_key in left_key))


def _is_explicit_detail_name(parent: str, child: str) -> bool:
    parent = "".join((parent or "").split())
    child = "".join((child or "").split())
    if not parent or not child.startswith(parent) or len(child) == len(parent):
        return False
    return child[len(parent)] in "-_—－（）()"


def reconcile_journal_to_trial_balance(
    pairs: tuple[JournalPair, ...] | list[JournalPair],
    balances: tuple[AccountBalance, ...] | list[AccountBalance],
) -> LedgerReconciliationResult:
    journal_debit, journal_credit = _journal_totals(pairs)
    balance_debit: dict[str, int] = defaultdict(int)
    balance_credit: dict[str, int] = defaultdict(int)
    differences: list[LedgerDifference] = []

    for row in balances:
        balance_debit[row.account_name] += row.debit_turnover_minor
        balance_credit[row.account_name] += row.credit_turnover_minor
        debit_closing = (
            row.opening_balance_minor
            + row.debit_turnover_minor
            - row.credit_turnover_minor
        )
        credit_closing = (
            row.opening_balance_minor
            + row.credit_turnover_minor
            - row.debit_turnover_minor
        )
        if row.closing_balance_minor not in {debit_closing, credit_closing}:
            closest = min(
                (debit_closing, credit_closing),
                key=lambda value: abs(row.closing_balance_minor - value),
            )
            differences.append(
                LedgerDifference(
                    account_name=row.account_name,
                    side="balance",
                    expected_minor=row.closing_balance_minor,
                    actual_minor=closest,
                    difference_minor=closest - row.closing_balance_minor,
                    kind="balance_equation",
                )
            )

    balance_names = set(balance_debit) | set(balance_credit)
    journal_names = set(journal_debit) | set(journal_credit)
    consumed_balance_names: set[str] = set()
    ambiguous_journal_names: set[str] = set()
    for journal_name in sorted(journal_names - balance_names):
        related_names = tuple(
            balance_name
            for balance_name in balance_names
            if _related_account_names(journal_name, balance_name)
        )
        ancestor_names = tuple(
            name for name in related_names
            if _account_key(name) in _account_key(journal_name)
        )
        detail_names = tuple(
            name for name in related_names
            if _account_key(journal_name) in _account_key(name)
        )
        if ancestor_names:
            max_length = max(len(_account_key(value)) for value in ancestor_names)
            most_specific = tuple(
                value for value in ancestor_names
                if len(_account_key(value)) == max_length
            )
            if len(most_specific) != 1:
                ambiguous_journal_names.add(journal_name)
                differences.append(LedgerDifference(
                    account_name=f"{journal_name} ↔ {'、'.join(sorted(most_specific))}",
                    side="both",
                    expected_minor=0,
                    actual_minor=journal_debit.get(journal_name, 0) + journal_credit.get(journal_name, 0),
                    difference_minor=journal_debit.get(journal_name, 0) + journal_credit.get(journal_name, 0),
                    kind="ambiguous_account_granularity",
                ))
                continue
            related = most_specific[0]
            remapped_debit = journal_debit.pop(journal_name, 0)
            remapped_credit = journal_credit.pop(journal_name, 0)
            journal_debit[related] = journal_debit.get(related, 0) + remapped_debit
            journal_credit[related] = journal_credit.get(related, 0) + remapped_credit
            differences.append(LedgerDifference(
                account_name=f"{journal_name} ↔ {related}",
                side="both",
                expected_minor=balance_debit.get(related, 0) + balance_credit.get(related, 0),
                actual_minor=remapped_debit + remapped_credit,
                difference_minor=0,
                kind="account_name_granularity",
            ))
        elif detail_names and all(
            _is_explicit_detail_name(journal_name, name)
            for name in detail_names
        ):
            balance_debit[journal_name] = sum(balance_debit.get(name, 0) for name in detail_names)
            balance_credit[journal_name] = sum(balance_credit.get(name, 0) for name in detail_names)
            consumed_balance_names.update(detail_names)
            differences.append(LedgerDifference(
                account_name=f"{journal_name} ↔ {'、'.join(sorted(detail_names))}",
                side="both",
                expected_minor=balance_debit[journal_name] + balance_credit[journal_name],
                actual_minor=journal_debit.get(journal_name, 0) + journal_credit.get(journal_name, 0),
                difference_minor=0,
                kind="account_name_granularity",
            ))
        elif related_names:
            ambiguous_journal_names.add(journal_name)
            differences.append(LedgerDifference(
                account_name=f"{journal_name} ↔ {'、'.join(sorted(related_names))}",
                side="both",
                expected_minor=sum(
                    balance_debit.get(name, 0) + balance_credit.get(name, 0)
                    for name in related_names
                ),
                actual_minor=journal_debit.get(journal_name, 0) + journal_credit.get(journal_name, 0),
                difference_minor=journal_debit.get(journal_name, 0) + journal_credit.get(journal_name, 0),
                kind="ambiguous_account_granularity",
            ))
    balance_names = (set(balance_debit) | set(balance_credit)) - consumed_balance_names
    journal_names = set(journal_debit) | set(journal_credit)
    for name in sorted((journal_names - balance_names) - ambiguous_journal_names):
        amount = journal_debit.get(name, 0) + journal_credit.get(name, 0)
        differences.append(
            LedgerDifference(
                account_name=name,
                side="both",
                expected_minor=0,
                actual_minor=amount,
                difference_minor=amount,
                kind="missing_trial_balance",
            )
        )

    for name in sorted((balance_names | journal_names) - ambiguous_journal_names):
        for side, journal, trial_balance in (
            ("debit", journal_debit, balance_debit),
            ("credit", journal_credit, balance_credit),
        ):
            actual = journal.get(name, 0)
            expected = trial_balance.get(name, 0)
            if actual != expected:
                differences.append(
                    LedgerDifference(
                        account_name=name,
                        side=side,
                        expected_minor=expected,
                        actual_minor=actual,
                        difference_minor=actual - expected,
                    )
                )

    ordered = tuple(
        sorted(
            differences,
            key=lambda item: (item.account_name, item.kind, item.side),
        )
    )
    return LedgerReconciliationResult(
        is_reconciled=not any(item.difference_minor for item in ordered),
        differences=ordered,
    )
