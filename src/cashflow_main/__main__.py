"""现金流量表主表工作底稿稳定命令入口。"""

import argparse
import json
from pathlib import Path

from .contracts import EnterpriseType
from .input_adapter import normalize_inputs
from .output import write_cashflow_statement
from .pipeline import (
    RunConfig,
    finalize_run,
    get_status,
    load_review_context,
    load_run_artifacts,
    load_validation_report,
    prepare_run,
)
from .storage import manifest_from_dict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cas-cashflow-main-workpaper")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="准备、计算、复核并输出暂编或最终正表")
    prepare.add_argument("--manifest", type=Path)
    prepare.add_argument("--select", action="store_true")
    prepare.add_argument("--run-dir", type=Path)
    finalize = commands.add_parser("finalize", help="录入确认结果后完成运行")
    finalize.add_argument("--run-dir", type=Path, required=True)
    finalize.add_argument("--decisions", type=Path, required=True)
    status = commands.add_parser("status", help="查看运行状态")
    status.add_argument("--run-dir", type=Path, required=True)
    return parser


def _load_config(args) -> tuple[RunConfig, Path]:
    if args.select:
        import sys
        sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
        from select_paths import select_manifest
        raw = select_manifest()
    elif args.manifest:
        raw = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    else:
        raise ValueError("prepare必须指定--manifest或--select")
    manifest = manifest_from_dict(raw)
    enterprise = EnterpriseType(raw["enterprise_type"]) if raw.get("enterprise_type") else None
    config = RunConfig(manifest, enterprise, str(raw.get("entity_name", "")), str(raw.get("period", "")))
    run_dir = args.run_dir or Path(str(raw.get("output_dir", "."))) / "现金流量表运行"
    return config, run_dir


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        config, run_dir = _load_config(args)
        result = prepare_run(config, run_dir)
        output = None
        if result.calculation and result.statement_kind in {"暂编", "最终"}:
            bundle = normalize_inputs(config.manifest)
            output = write_cashflow_statement(
                result.calculation,
                bundle.prior_cashflow,
                config,
                result.validation_report,
                result.statement_kind,
                run_dir / f"现金流量表_{result.statement_kind}.xlsx",
                bridge=result.adjustment_bridge,
                ledger_reconciliation=result.ledger_reconciliation,
                review_context=result.review_context,
            )
        print(json.dumps({"status": result.status.value, "statement_kind": result.statement_kind, "run_dir": str(run_dir), "output": str(output) if output else None}, ensure_ascii=False))
    elif args.command == "finalize":
        decisions = json.loads(args.decisions.read_text(encoding="utf-8-sig"))
        result = finalize_run(args.run_dir, decisions)
        state = json.loads((args.run_dir / "state.json").read_text(encoding="utf-8-sig"))
        manifest = manifest_from_dict(state["manifest"])
        config = RunConfig(manifest, result.enterprise_type, state.get("entity_name", ""), state.get("period", ""))
        calculation, bridge, ledger = load_run_artifacts(args.run_dir)
        validation = load_validation_report(args.run_dir)
        review_context = load_review_context(args.run_dir)
        bundle = normalize_inputs(manifest)
        output = write_cashflow_statement(
            calculation,
            bundle.prior_cashflow,
            config,
            validation,
            "最终",
            args.run_dir / "现金流量表_最终.xlsx",
            bridge=bridge,
            ledger_reconciliation=ledger,
            review_context=review_context,
        )
        print(json.dumps({"status": result.status.value, "statement_kind": result.statement_kind, "output": str(output)}, ensure_ascii=False))
    else:
        result = get_status(args.run_dir)
        print(json.dumps({"status": result.status.value, "statement_kind": result.statement_kind, "enterprise_type": result.enterprise_type.value}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
