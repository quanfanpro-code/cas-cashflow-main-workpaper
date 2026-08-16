"""校验公式核验记录和规则包之间的完整对应关系。"""

import argparse
import json
from pathlib import Path

REQUIRED_EVIDENCE = {
    "knowledge_base",
    "pdf_article",
    "second_slides",
    "second_workbook",
    "first_workbook",
}
REQUIRED_FIELDS = {
    "verification_id",
    "enterprise_type",
    "cashflow_item_id",
    "candidate_formula",
    "evidence",
    "issues",
    "corrected_formula",
    "conclusion",
    "reviewed_at",
}


def validate_formula_record(record: dict[str, object]) -> list[str]:
    errors = [
        f"缺少字段：{field}"
        for field in sorted(REQUIRED_FIELDS - record.keys())
    ]
    evidence = record.get("evidence", {})
    if not isinstance(evidence, dict):
        errors.append("evidence必须是对象")
    else:
        errors.extend(
            f"缺少证据类别：{field}"
            for field in sorted(REQUIRED_EVIDENCE - evidence.keys())
        )
    corrected = record.get("corrected_formula", {})
    if not isinstance(corrected, dict):
        errors.append("corrected_formula必须是对象")
    else:
        for field in (
            "components",
            "gross_or_net",
            "noncash_exclusions",
            "special_adjustments",
            "restricted_cash_treatment",
        ):
            if field not in corrected:
                errors.append(f"修正公式缺少：{field}")
    if record.get("conclusion") not in {"verified", "unresolved"}:
        errors.append("conclusion必须是verified或unresolved")
    for issue in record.get("issues", []):
        if not isinstance(issue, dict):
            errors.append("issues中的每项必须是对象")
            continue
        if issue.get("type") == "title_typo" and issue.get("affects_formula") is True:
            errors.append("标题笔误不得自动认定为公式错误")
    return errors


def load_registry(path: Path) -> dict[str, dict[str, object]]:
    records = {}
    if not path.exists():
        return records
    files = [path] if path.is_file() else sorted(path.glob("*.jsonl"))
    for file in files:
        for line_number, line in enumerate(
            file.read_text(encoding="utf-8-sig").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            record = json.loads(line)
            errors = validate_formula_record(record)
            if errors:
                raise ValueError(
                    f"{file.name}:{line_number}：" + "；".join(errors)
                )
            verification_id = str(record["verification_id"])
            if verification_id in records:
                raise ValueError(f"核验编号重复：{verification_id}")
            records[verification_id] = record
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--rule-pack", type=Path)
    parser.add_argument("--all-rule-packs", action="store_true")
    parser.add_argument("--require-zero-unresolved", action="store_true")
    args = parser.parse_args()
    if args.all_rule_packs:
        records = load_registry(Path(__file__).parents[1] / "references" / "公式核验")
    else:
        if args.registry is None:
            parser.error("非全量模式必须提供 --registry")
        records = load_registry(args.registry)
    unresolved = sum(
        record["conclusion"] != "verified" for record in records.values()
    )
    rule_files = []
    if args.rule_pack:
        rule_files.append(args.rule_pack)
    if args.all_rule_packs:
        rule_files.extend(sorted((Path(__file__).parents[1] / "rules").glob("*_v1.json")))
    referenced = set()
    missing_evidence = []
    for path in rule_files:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        for item in raw["items"]:
            verification_id = item["verification_record_id"]
            referenced.add(verification_id)
            if verification_id not in records:
                missing_evidence.append(f"{path.name}:{item['item_id']}:{verification_id}")
    orphan_rule = sorted(referenced - records.keys())
    print(
        f"records={len(records)} unresolved={unresolved} "
        f"missing_evidence={len(missing_evidence)} orphan_rule={len(orphan_rule)}"
    )
    if missing_evidence:
        for value in missing_evidence:
            print(f"missing:{value}")
    if (args.require_zero_unresolved and unresolved) or missing_evidence or orphan_rule:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
