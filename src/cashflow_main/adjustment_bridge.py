"""建立账面报表到审定报表的调整桥。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AdjustmentRecord:
    adjustment_id: str
    report_item: str
    amount_minor: int
    adjustment_type: str
    nature: str | None = None
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateAdjustment:
    candidate_id: str
    report_item: str
    amount_minor: int
    cash_nature: str
    reason: str


@dataclass(frozen=True)
class AdjustmentBridgeRow:
    report_item: str
    book_minor: int
    book_to_report_minor: int
    audit_adjustment_minor: int
    audited_minor: int
    total_difference_minor: int
    unexplained_minor: int
    matched_adjustments: tuple[AdjustmentRecord, ...]
    candidate_adjustments: tuple[CandidateAdjustment, ...]


@dataclass(frozen=True)
class AdjustmentBridgeResult:
    rows: tuple[AdjustmentBridgeRow, ...]
    orphan_adjustments: tuple[AdjustmentRecord, ...]
    is_amount_reconciled: bool


def _candidates(
    report_item: str,
    amount_minor: int,
) -> tuple[CandidateAdjustment, ...]:
    if amount_minor == 0:
        return ()
    base = report_item.replace(" ", "")
    return (
        CandidateAdjustment(
            candidate_id=f"CAND:{base}:NONCASH",
            report_item=report_item,
            amount_minor=amount_minor,
            cash_nature="noncash_or_reclassification",
            reason="金额差额已确定，但现有资料尚未证明涉及现金。",
        ),
        CandidateAdjustment(
            candidate_id=f"CAND:{base}:CASH",
            report_item=report_item,
            amount_minor=amount_minor,
            cash_nature="cash_effect_possible",
            reason="如调整真实改变现金类科目，需要继续追查对应依据。",
        ),
    )


def build_adjustment_bridge(
    book: dict[str, int],
    audited: dict[str, int],
    book_to_report: tuple[AdjustmentRecord, ...] | list[AdjustmentRecord],
    audit_adjustments: tuple[AdjustmentRecord, ...] | list[AdjustmentRecord],
) -> AdjustmentBridgeResult:
    report_items = set(book) | set(audited)
    all_adjustments = tuple(book_to_report) + tuple(audit_adjustments)
    orphan = tuple(
        item for item in all_adjustments if item.report_item not in report_items
    )
    rows = []
    for report_item in sorted(report_items):
        matched_book = tuple(
            item for item in book_to_report if item.report_item == report_item
        )
        matched_audit = tuple(
            item for item in audit_adjustments if item.report_item == report_item
        )
        book_minor = book.get(report_item, 0)
        audited_minor = audited.get(report_item, 0)
        book_to_report_minor = sum(item.amount_minor for item in matched_book)
        audit_minor = sum(item.amount_minor for item in matched_audit)
        difference = audited_minor - book_minor
        unexplained = difference - book_to_report_minor - audit_minor
        rows.append(
            AdjustmentBridgeRow(
                report_item=report_item,
                book_minor=book_minor,
                book_to_report_minor=book_to_report_minor,
                audit_adjustment_minor=audit_minor,
                audited_minor=audited_minor,
                total_difference_minor=difference,
                unexplained_minor=unexplained,
                matched_adjustments=matched_book + matched_audit,
                candidate_adjustments=_candidates(report_item, unexplained),
            )
        )
    result_rows = tuple(rows)
    return AdjustmentBridgeResult(
        rows=result_rows,
        orphan_adjustments=orphan,
        is_amount_reconciled=(
            not orphan
            and all(row.unexplained_minor == 0 for row in result_rows)
        ),
    )
