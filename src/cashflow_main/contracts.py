"""现金流量表主表引擎的稳定数据合同。"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class EnterpriseType(StrEnum):
    GENERAL = "general"
    BANK = "bank"
    SECURITIES = "securities"
    INSURANCE = "insurance"
    OTHER_FINANCIAL = "other_financial"


class RunStatus(StrEnum):
    CREATED = "created"
    INPUT_SNAPSHOTTED = "input_snapshotted"
    NORMALIZED = "normalized"
    LEDGER_RECONCILED = "ledger_reconciled"
    STATEMENT_RECONCILED = "statement_reconciled"
    AWAITING_DECISION = "awaiting_decision"
    CALCULATED = "calculated"
    VALIDATED = "validated"
    PROVISIONAL = "provisional"
    EXPORTED = "exported"
    BLOCKED = "blocked"
    FAILED = "failed"


ALLOWED_TRANSITIONS = {
    RunStatus.CREATED: {
        RunStatus.INPUT_SNAPSHOTTED,
        RunStatus.BLOCKED,
        RunStatus.FAILED,
    },
    RunStatus.INPUT_SNAPSHOTTED: {
        RunStatus.NORMALIZED,
        RunStatus.BLOCKED,
        RunStatus.FAILED,
    },
    RunStatus.NORMALIZED: {
        RunStatus.LEDGER_RECONCILED,
        RunStatus.BLOCKED,
        RunStatus.FAILED,
    },
    RunStatus.LEDGER_RECONCILED: {
        RunStatus.STATEMENT_RECONCILED,
        RunStatus.BLOCKED,
        RunStatus.FAILED,
    },
    RunStatus.STATEMENT_RECONCILED: {
        RunStatus.AWAITING_DECISION,
        RunStatus.CALCULATED,
        RunStatus.BLOCKED,
        RunStatus.FAILED,
    },
    RunStatus.AWAITING_DECISION: {
        RunStatus.CALCULATED,
        RunStatus.PROVISIONAL,
        RunStatus.BLOCKED,
        RunStatus.FAILED,
    },
    RunStatus.CALCULATED: {
        RunStatus.VALIDATED,
        RunStatus.PROVISIONAL,
        RunStatus.BLOCKED,
        RunStatus.FAILED,
    },
    RunStatus.VALIDATED: {RunStatus.EXPORTED, RunStatus.FAILED},
    RunStatus.PROVISIONAL: {
        RunStatus.AWAITING_DECISION,
        RunStatus.CALCULATED,
        RunStatus.EXPORTED,
        RunStatus.FAILED,
    },
    RunStatus.EXPORTED: set(),
    RunStatus.BLOCKED: set(),
    RunStatus.FAILED: set(),
}


@dataclass
class RunState:
    run_id: str
    status: RunStatus = RunStatus.CREATED

    def advance(self, target: RunStatus) -> None:
        if target not in ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status} -> {target}")
        self.status = target


@dataclass(frozen=True)
class InputManifest:
    audited_balance_sheet_path: Path
    audited_income_statement_path: Path
    trial_balance_path: Path
    journal_pairs_path: Path
    prior_cashflow_path: Path
    display_unit: str
    currency: str
    performance_materiality_minor: int
    book_to_report_adjustments_path: Path | None = None
    audit_adjustments_path: Path | None = None

    def required_paths(self) -> dict[str, Path]:
        return {
            "audited_balance_sheet": self.audited_balance_sheet_path,
            "audited_income_statement": self.audited_income_statement_path,
            "trial_balance": self.trial_balance_path,
            "journal_pairs": self.journal_pairs_path,
            "prior_cashflow": self.prior_cashflow_path,
        }

    def input_paths(self) -> dict[str, Path]:
        paths = self.required_paths()
        if self.book_to_report_adjustments_path:
            paths["book_to_report_adjustments"] = self.book_to_report_adjustments_path
        if self.audit_adjustments_path:
            paths["audit_adjustments"] = self.audit_adjustments_path
        return paths


@dataclass(frozen=True)
class StatementLine:
    item_name: str
    current_minor: int
    prior_minor: int | None = None


@dataclass(frozen=True)
class AccountBalance:
    account_code: str
    account_name: str
    opening_balance_minor: int
    debit_turnover_minor: int
    credit_turnover_minor: int
    closing_balance_minor: int
    original_fields: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class JournalPair:
    debit_account_name: str
    credit_account_name: str
    amount_minor: int
    original_fields: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedAdjustment:
    adjustment_id: str
    report_item: str
    amount_minor: int
    adjustment_type: str
    nature: str | None = None
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NormalizedInputBundle:
    audited_balance_sheet: tuple[StatementLine, ...]
    audited_income_statement: tuple[StatementLine, ...]
    trial_balance: tuple[AccountBalance, ...]
    journal_pairs: tuple[JournalPair, ...]
    prior_cashflow: tuple[StatementLine, ...]
    book_to_report_adjustments: tuple[NormalizedAdjustment, ...] = ()
    audit_adjustments: tuple[NormalizedAdjustment, ...] = ()
