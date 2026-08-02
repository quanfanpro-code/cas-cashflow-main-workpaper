from cashflow_main.adjustment_bridge import (
    AdjustmentRecord,
    build_adjustment_bridge,
)


def adjustment(
    amount_minor: int,
    *,
    adjustment_id: str,
    adjustment_type: str,
) -> AdjustmentRecord:
    return AdjustmentRecord(
        adjustment_id=adjustment_id,
        report_item="应收账款",
        amount_minor=amount_minor,
        adjustment_type=adjustment_type,
    )


def test_bridge_calculates_difference_without_customer_schedule():
    result = build_adjustment_bridge(
        book={"应收账款": 100_000},
        audited={"应收账款": 90_000},
        book_to_report=(),
        audit_adjustments=(),
    )
    row = result.rows[0]
    assert row.total_difference_minor == -10_000
    assert row.unexplained_minor == -10_000
    assert row.candidate_adjustments


def test_provided_adjustments_reconcile_to_audited_amount():
    result = build_adjustment_bridge(
        book={"应收账款": 100_000},
        audited={"应收账款": 90_000},
        book_to_report=(
            adjustment(-4_000, adjustment_id="BTR-1", adjustment_type="book_to_report"),
        ),
        audit_adjustments=(
            adjustment(-6_000, adjustment_id="AUD-1", adjustment_type="audit"),
        ),
    )
    assert result.is_amount_reconciled
    assert result.rows[0].unexplained_minor == 0
    assert result.rows[0].book_to_report_minor == -4_000
    assert result.rows[0].audit_adjustment_minor == -6_000


def test_adjustment_for_unknown_report_item_is_not_silently_dropped():
    result = build_adjustment_bridge(
        book={"应收账款": 100_000},
        audited={"应收账款": 100_000},
        book_to_report=(
            AdjustmentRecord("BTR-X", "其他项目", 1_000, "book_to_report"),
        ),
        audit_adjustments=(),
    )
    assert result.orphan_adjustments[0].adjustment_id == "BTR-X"
    assert not result.is_amount_reconciled
