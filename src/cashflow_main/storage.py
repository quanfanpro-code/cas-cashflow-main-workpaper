"""运行目录的输入哈希和原子状态存储。"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .contracts import InputManifest


class InputChangedError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_inputs(manifest: InputManifest) -> dict[str, str]:
    return {name: sha256_file(path) for name, path in manifest.input_paths().items()}


def assert_inputs_unchanged(saved: dict[str, str], manifest: InputManifest) -> None:
    if snapshot_inputs(manifest) != saved:
        raise InputChangedError("输入文件已变化，必须重新准备运行")


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def manifest_to_dict(manifest: InputManifest) -> dict[str, object]:
    return {
        "audited_balance_sheet_path": str(manifest.audited_balance_sheet_path),
        "audited_income_statement_path": str(manifest.audited_income_statement_path),
        "trial_balance_path": str(manifest.trial_balance_path),
        "journal_pairs_path": str(manifest.journal_pairs_path),
        "prior_cashflow_path": str(manifest.prior_cashflow_path),
        "display_unit": manifest.display_unit,
        "currency": manifest.currency,
        "performance_materiality_minor": manifest.performance_materiality_minor,
        "book_to_report_adjustments_path": str(manifest.book_to_report_adjustments_path) if manifest.book_to_report_adjustments_path else None,
        "audit_adjustments_path": str(manifest.audit_adjustments_path) if manifest.audit_adjustments_path else None,
    }


def manifest_from_dict(raw: dict[str, object]) -> InputManifest:
    optional = lambda name: Path(str(raw[name])) if raw.get(name) else None
    return InputManifest(
        Path(str(raw["audited_balance_sheet_path"])), Path(str(raw["audited_income_statement_path"])),
        Path(str(raw["trial_balance_path"])), Path(str(raw["journal_pairs_path"])), Path(str(raw["prior_cashflow_path"])),
        str(raw["display_unit"]), str(raw["currency"]), int(raw["performance_materiality_minor"]),
        optional("book_to_report_adjustments_path"), optional("audit_adjustments_path"),
    )
