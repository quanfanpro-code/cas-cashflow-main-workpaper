import time
import tracemalloc

from cashflow_main.contracts import AccountBalance, JournalPair
from cashflow_main.ledger_reconciliation import reconcile_journal_to_trial_balance


def test_one_million_pairs_reconcile_without_workbook_intermediate(tmp_path):
    def pairs():
        for _ in range(1_000_000):
            yield JournalPair("银行存款", "营业收入", 1)
    balances = (
        AccountBalance("1002", "银行存款", 0, 1_000_000, 0, 1_000_000),
        AccountBalance("6001", "营业收入", 0, 0, 1_000_000, 1_000_000),
    )
    tracemalloc.start(); started = time.perf_counter()
    result = reconcile_journal_to_trial_balance(pairs(), balances)
    elapsed = time.perf_counter() - started; _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    assert result.is_reconciled
    assert elapsed < 120
    assert peak < 2_500 * 1024 * 1024
    assert not list(tmp_path.rglob("*中间底稿*.xlsx"))

