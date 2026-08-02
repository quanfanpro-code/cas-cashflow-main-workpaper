from dataclasses import replace

from cashflow_main.contracts import AccountBalance, JournalPair
from cashflow_main.ledger_reconciliation import reconcile_journal_to_trial_balance


def sample_pairs():
    return (
        JournalPair("银行存款", "应收账款", 100_000),
    )


def sample_balances():
    return (
        AccountBalance("1002", "银行存款", 500_000, 100_000, 0, 600_000),
        AccountBalance("1122", "应收账款", 500_000, 0, 100_000, 600_000),
    )


def test_paired_rows_reconcile_both_sides_to_trial_balance():
    result = reconcile_journal_to_trial_balance(sample_pairs(), sample_balances())
    assert result.is_reconciled
    assert result.differences == ()


def test_difference_names_account_side_and_amount():
    balances = list(sample_balances())
    balances[0] = replace(
        balances[0],
        debit_turnover_minor=balances[0].debit_turnover_minor + 1,
    )
    result = reconcile_journal_to_trial_balance(sample_pairs(), balances)
    assert not result.is_reconciled
    difference = next(item for item in result.differences if item.side == "debit")
    assert difference.account_name == "银行存款"
    assert difference.difference_minor == -1


def test_account_in_journal_but_not_trial_balance_is_reported():
    pairs = sample_pairs() + (JournalPair("临时科目", "银行存款", 50_000),)
    result = reconcile_journal_to_trial_balance(pairs, sample_balances())
    assert any(
        item.account_name == "临时科目" and item.kind == "missing_trial_balance"
        for item in result.differences
    )


def test_account_name_granularity_difference_has_explicit_diagnostic():
    pairs = (JournalPair("银行存款-基本户", "应收账款", 100_000),)
    result = reconcile_journal_to_trial_balance(pairs, sample_balances())
    assert any(item.kind == "account_name_granularity" for item in result.differences)
    assert result.is_reconciled
