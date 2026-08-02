"""按财政部正式格式从现有规则中生成金融企业修订规则包。"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path


FINANCIAL_TYPES = ("bank", "securities", "insurance", "other_financial")


def component(component_id: str, tag: str, sign: int = 1, *, net: bool = False) -> dict:
    return {
        "component_id": component_id,
        "operation": "fact_amount",
        "sign": sign,
        "source_scope": "fact_ledger",
        "selector": {"tags_any": [tag], "industry_exclusive": True},
        "occupancy_policy": "exclusive",
        "gross_or_net": "net" if net else "gross",
        "noncash_exclusions": ["非现金结算", "公允价值变动", "应计未收未付"] if net else [],
        "special_adjustments": ["仅按现金及现金等价物实际收付额列示"],
        "restricted_cash_treatment": ["受限资金不满足现金及现金等价物定义时剔除"],
    }


def gross_item(item_id: str, name: str, tag: str, verification_id: str) -> dict:
    return {
        "item_id": item_id,
        "name": name,
        "section": "operating",
        "display_order": 0,
        "verification_record_id": verification_id,
        "components": [component(f"{item_id}-C01", tag)],
    }


def net_item(
    item_id: str,
    name: str,
    positive_tag: str,
    negative_tag: str,
    verification_id: str,
    *,
    section: str = "operating",
) -> dict:
    return {
        "item_id": item_id,
        "name": name,
        "section": section,
        "display_order": 0,
        "verification_record_id": verification_id,
        "components": [
            component(f"{item_id}-C01", positive_tag, 1, net=True),
            component(f"{item_id}-C02", negative_tag, -1, net=True),
        ],
    }


def positive_net_item(
    item_id: str,
    name: str,
    positive_tag: str,
    negative_tag: str,
    verification_id: str,
    *,
    section: str = "operating",
) -> dict:
    """仅在指定方向净额为正时列示；反方向由另一项目承接。"""
    return {
        "item_id": item_id,
        "name": name,
        "section": section,
        "display_order": 0,
        "verification_record_id": verification_id,
        "components": [{
            "component_id": f"{item_id}-C01",
            "operation": "net_fact_amount",
            "sign": 1,
            "source_scope": "fact_ledger",
            "selector": {
                "positive_tags_any": [positive_tag],
                "negative_tags_any": [negative_tag],
                "positive_only": True,
                "industry_exclusive": True,
            },
            "occupancy_policy": "exclusive",
            "gross_or_net": "net",
            "noncash_exclusions": ["非现金结算", "公允价值变动", "应计未收未付"],
            "special_adjustments": ["仅在本项目方向的现金净额为正时列示，反方向由对应项目承接"],
            "restricted_cash_treatment": ["受限资金不满足现金及现金等价物定义时剔除"],
        }],
    }


def subtotal(item_id: str, name: str, section: str, item_ids: list[str], verification_id: str, signs=None) -> dict:
    selector = {"item_ids": item_ids}
    if signs is not None:
        selector["signs"] = signs
    return {
        "item_id": item_id,
        "name": name,
        "section": section,
        "display_order": 0,
        "verification_record_id": verification_id,
        "is_subtotal": True,
        "subtotal_of": item_ids,
        "components": [{
            "component_id": f"{item_id}-C01",
            "operation": "subtotal",
            "sign": 1,
            "source_scope": "calculated_items",
            "selector": selector,
            "occupancy_policy": "shared_control",
            "gross_or_net": "not_applicable",
            "noncash_exclusions": [],
            "special_adjustments": [],
            "restricted_cash_treatment": [],
        }],
    }


def employee_tax_other(prefix: str, verification_id: str) -> list[dict]:
    return [
        gross_item(f"{prefix}-EMP", "支付给职工及为职工支付的现金", "employee_cash_payment", verification_id),
        gross_item(f"{prefix}-TAX", "支付的各项税费", "tax_cash_payment", verification_id),
        gross_item(f"{prefix}-OTHER-OUT", "支付其他与经营活动有关的现金", "other_operating_cash_paid", verification_id),
    ]


def common_operating_out(prefix: str, verification_id: str) -> list[dict]:
    return [
        net_item(f"{prefix}-TRADING", "为交易目的而持有的金融资产净增加额", "trading_asset_cash_paid", "trading_asset_cash_received", verification_id),
        net_item(f"{prefix}-LENT", "拆出资金净增加额", "funds_lent_cash_paid", "funds_lent_cash_received", verification_id),
        net_item(f"{prefix}-REVERSE-REPO", "返售业务资金净增加额", "reverse_repo_cash_paid", "reverse_repo_cash_received", verification_id),
        gross_item(f"{prefix}-INTEREST-OUT", "支付利息、手续费及佣金的现金", "financial_interest_fee_cash_paid", verification_id),
    ]


def operating_items(industry: str, verification_id: str) -> tuple[list[dict], list[dict]]:
    if industry == "bank":
        inflow = [
            net_item("CFO-B01", "客户存款和同业存放款项净增加额", "customer_deposit_cash_received", "customer_deposit_cash_paid", verification_id),
            net_item("CFO-B02", "向中央银行借款净增加额", "central_bank_borrowing_cash_received", "central_bank_borrowing_cash_repaid", verification_id),
            net_item("CFO-B03", "向其他金融机构拆入资金净增加额", "other_financial_borrowing_cash_received", "other_financial_borrowing_cash_repaid", verification_id),
            gross_item("CFO-B04", "收取利息、手续费及佣金的现金", "financial_interest_fee_cash_received", verification_id),
            gross_item("CFO-B05", "收到其他与经营活动有关的现金", "other_operating_cash_received", verification_id),
        ]
        outflow = [
            net_item("CFO-B06", "客户贷款及垫款净增加额", "customer_loan_cash_paid", "customer_loan_cash_received", verification_id),
            net_item("CFO-B07", "存放中央银行和同业款项净增加额", "central_interbank_deposit_cash_paid", "central_interbank_deposit_cash_received", verification_id),
            *common_operating_out("CFO-B", verification_id),
            *employee_tax_other("CFO-B", verification_id),
        ]
    elif industry == "securities":
        inflow = [
            positive_net_item("CFO-S-MARGIN-DECREASE", "融出资金净减少额", "margin_financing_cash_received", "margin_financing_cash_paid", verification_id),
            positive_net_item("CFO-S-REVERSE-REPO-DECREASE", "返售业务资金净减少额", "reverse_repo_cash_received", "reverse_repo_cash_paid", verification_id),
            gross_item("CFO-S02", "收取利息、手续费及佣金的现金", "financial_interest_fee_cash_received", verification_id),
            net_item("CFO-S03", "拆入资金净增加额", "interbank_borrowing_cash_received", "interbank_borrowing_cash_repaid", verification_id),
            net_item("CFO-S04", "回购业务资金净增加额", "repo_cash_received", "repo_cash_paid", verification_id),
            net_item("CFO-S05", "代理买卖证券收到的现金净额", "brokerage_funds_cash_received", "brokerage_funds_cash_paid", verification_id),
            gross_item("CFO-S06", "收到其他与经营活动有关的现金", "other_operating_cash_received", verification_id),
        ]
        outflow = [
            net_item("CFO-S-TRADING", "为交易目的而持有的金融资产净增加额", "trading_asset_cash_paid", "trading_asset_cash_received", verification_id),
            net_item("CFO-S-LENT", "拆出资金净增加额", "funds_lent_cash_paid", "funds_lent_cash_received", verification_id),
            positive_net_item("CFO-S-MARGIN-INCREASE", "融出资金净增加额", "margin_financing_cash_paid", "margin_financing_cash_received", verification_id),
            positive_net_item("CFO-S-REVERSE-REPO", "返售业务资金净增加额", "reverse_repo_cash_paid", "reverse_repo_cash_received", verification_id),
            gross_item("CFO-S-INTEREST-OUT", "支付利息、手续费及佣金的现金", "financial_interest_fee_cash_paid", verification_id),
            *employee_tax_other("CFO-S", verification_id),
        ]
    elif industry == "insurance":
        inflow = [
            gross_item("CFO-I01", "收到原保险合同保费取得的现金", "direct_insurance_premium_receipt", verification_id),
            positive_net_item("CFO-I02", "收到再保业务现金净额", "reinsurance_cash_received", "reinsurance_cash_paid", verification_id),
            positive_net_item("CFO-I03", "保户储金及投资款净增加额", "policyholder_deposit_cash_received", "policyholder_deposit_cash_paid", verification_id),
            gross_item("CFO-I05", "收取利息、手续费及佣金的现金", "financial_interest_fee_cash_received", verification_id),
            gross_item("CFO-I06", "收到其他与经营活动有关的现金", "other_operating_cash_received", verification_id),
        ]
        outflow = [
            gross_item("CFO-I07", "支付原保险合同赔付款项的现金", "direct_insurance_claim_cash_paid", verification_id),
            positive_net_item("CFO-I08", "支付再保业务现金净额", "reinsurance_cash_paid", "reinsurance_cash_received", verification_id),
            positive_net_item("CFO-I-POLICY-OUT", "保户储金及投资款净减少额", "policyholder_deposit_cash_paid", "policyholder_deposit_cash_received", verification_id),
            gross_item("CFO-I09", "支付保单红利的现金", "policy_dividend_cash_paid", verification_id),
            net_item("CFO-I-TRADING", "为交易目的而持有的金融资产净增加额", "trading_asset_cash_paid", "trading_asset_cash_received", verification_id),
            net_item("CFO-I-LENT", "拆出资金净增加额", "funds_lent_cash_paid", "funds_lent_cash_received", verification_id),
            net_item("CFO-I-REVERSE-REPO", "返售业务资金净增加额", "reverse_repo_cash_paid", "reverse_repo_cash_received", verification_id),
            gross_item("CFO-I-FEE", "支付手续费及佣金的现金", "financial_fee_cash_paid", verification_id),
            *employee_tax_other("CFO-I", verification_id),
        ]
    else:
        inflow = [
            gross_item("CFO-O-SALES", "销售商品、提供劳务收到的现金", "financial_sales_service_cash_received", verification_id),
            net_item("CFO-O-BORROW", "拆入资金净增加额", "interbank_borrowing_cash_received", "interbank_borrowing_cash_repaid", verification_id),
            net_item("CFO-O-REPO", "回购业务资金净增加额", "repo_cash_received", "repo_cash_paid", verification_id),
            gross_item("CFO-O-INTEREST", "收取利息、手续费及佣金的现金", "financial_interest_fee_cash_received", verification_id),
            gross_item("CFO-O02", "收到其他与经营活动有关的现金", "other_operating_cash_received", verification_id),
        ]
        outflow = [
            *common_operating_out("CFO-O", verification_id),
            *employee_tax_other("CFO-O", verification_id),
        ]
    return inflow, outflow


def old_insurance_investing_items(verification_id: str) -> list[dict]:
    return [
        net_item("CFI-I-REVERSE-REPO", "返售业务资金净增加额", "reverse_repo_cash_paid", "reverse_repo_cash_received", verification_id, section="investing"),
        net_item("CFI-I-PLEDGE", "质押贷款净增加额", "policy_pledge_loan_cash_paid", "policy_pledge_loan_cash_received", verification_id, section="investing"),
    ]


def financing_items(by_id: dict[str, dict], verification_id: str, *, insurance_repo: bool) -> list[dict]:
    capital = copy.deepcopy(by_id["CFF-01"])
    capital["components"] = [
        component("CFF-01-C01", "equity_investment_cash_received"),
        component("CFF-01-C02", "equity_issue_cost_cash_paid", -1, net=True),
    ]
    capital["verification_record_id"] = verification_id
    borrowing = copy.deepcopy(by_id["CFF-02"])
    borrowing["components"] = [component("CFF-02-C01", "short_term_borrowing_cash_received"), component("CFF-02-C02", "long_term_borrowing_cash_received")]
    borrowing["verification_record_id"] = verification_id
    bond = gross_item("CFF-BOND", "发行债券收到的现金", "bond_issue_cash_received", verification_id)
    bond["section"] = "financing"
    repo = net_item("CFF-I-REPO", "回购业务资金净增加额", "repo_cash_received", "repo_cash_paid", verification_id, section="financing") if insurance_repo else None
    result = [capital, borrowing, bond]
    if repo:
        result.append(repo)
    result.append(copy.deepcopy(by_id["CFF-03"]))
    return result


def rebuild_pack(raw: dict, industry: str) -> dict:
    result = copy.deepcopy(raw)
    by_id = {item["item_id"]: item for item in raw["items"]}
    verification_id = f"FV-{industry.upper()}-FORMAT-2018-R2"
    inflow, outflow = operating_items(industry, verification_id)
    items = [*inflow]
    items.append(subtotal("CFO-IN", "经营活动现金流入小计", "operating", [item["item_id"] for item in inflow], verification_id))
    items.extend(outflow)
    items.append(subtotal("CFO-OUT", "经营活动现金流出小计", "operating", [item["item_id"] for item in outflow], verification_id))
    items.append(subtotal("CFO-NET", "经营活动产生的现金流量净额", "operating", ["CFO-IN", "CFO-OUT"], verification_id, [1, -1]))

    investing_in = [copy.deepcopy(by_id[item_id]) for item_id in ("CFI-01", "CFI-02", "CFI-03", "CFI-05")]
    investing_out = [copy.deepcopy(by_id["CFI-07"])]
    if industry == "insurance":
        investing_out.extend(old_insurance_investing_items(verification_id))
    investing_out.extend(copy.deepcopy(by_id[item_id]) for item_id in ("CFI-06", "CFI-09"))
    items.extend(investing_in)
    items.append(subtotal("CFI-IN", "投资活动现金流入小计", "investing", [item["item_id"] for item in investing_in], verification_id))
    items.extend(investing_out)
    items.append(subtotal("CFI-OUT", "投资活动现金流出小计", "investing", [item["item_id"] for item in investing_out], verification_id))
    items.append(subtotal("CFI-NET", "投资活动产生的现金流量净额", "investing", ["CFI-IN", "CFI-OUT"], verification_id, [1, -1]))

    financing_in = financing_items(by_id, verification_id, insurance_repo=industry == "insurance")
    items.extend(financing_in)
    items.append(subtotal("CFF-IN", "筹资活动现金流入小计", "financing", [item["item_id"] for item in financing_in], verification_id))
    financing_out = [copy.deepcopy(by_id[item_id]) for item_id in ("CFF-04", "CFF-05", "CFF-06")]
    dividend_tags = financing_out[1]["components"][0]["selector"].get("tags_any", [])
    financing_out[1]["components"][0]["selector"]["tags_any"] = [
        tag for tag in dividend_tags if tag != "profit_distribution_cash_paid"
    ]
    if financing_out[-1]["components"]:
        tags = financing_out[-1]["components"][0]["selector"].get("tags_any", [])
        financing_out[-1]["components"][0]["selector"]["tags_any"] = [tag for tag in tags if tag != "financing_issue_cost_cash_paid"]
    items.extend(financing_out)
    items.append(subtotal("CFF-OUT", "筹资活动现金流出小计", "financing", [item["item_id"] for item in financing_out], verification_id))
    items.append(subtotal("CFF-NET", "筹资活动产生的现金流量净额", "financing", ["CFF-IN", "CFF-OUT"], verification_id, [1, -1]))
    items.extend(copy.deepcopy(by_id[item_id]) for item_id in ("FX", "NET-CASH", "OPENING-CASH", "CLOSING-CASH"))

    for order, item in enumerate(items, 1):
        item["display_order"] = order * 10
    result["version"] = "2.1.0"
    result["items"] = items
    result["statement_template"] = [
        {key: item[key] for key in ("item_id", "name", "section", "display_order")}
        for item in items
    ]
    return result


def insurance_2023_pack(old_pack: dict) -> dict:
    result = rebuild_pack(old_pack, "insurance")
    fmt = "FV-INSURANCE-2023-FORMAT"
    by_id = {item["item_id"]: item for item in result["items"]}
    inflow = [
        gross_item("CFO-I23-SALES", "销售商品、提供劳务收到的现金", "financial_sales_service_cash_received", fmt),
        net_item("CFO-I23-OTHER-BORROW", "向其他金融机构拆入资金净增加额", "other_financial_borrowing_cash_received", "other_financial_borrowing_cash_repaid", fmt),
        gross_item("CFO-I23-PREMIUM", "收到签发保险合同保费取得的现金", "insurance_contract_premium_cash_received", fmt),
        net_item("CFO-I23-INWARD", "收到分入再保险合同的现金净额", "inward_reinsurance_cash_received", "inward_reinsurance_cash_paid", fmt),
        net_item("CFO-I23-BORROW", "拆入资金净增加额", "interbank_borrowing_cash_received", "interbank_borrowing_cash_repaid", fmt),
        gross_item("CFO-I23-OTHER-IN", "收到其他与经营活动有关的现金", "other_operating_cash_received", fmt),
    ]
    outflow = [
        gross_item("CFO-I23-CLAIM", "支付签发保险合同赔款的现金", "insurance_contract_claim_cash_paid", fmt),
        net_item("CFO-I23-OUTWARD", "支付分出再保险合同的现金净额", "outward_reinsurance_cash_paid", "outward_reinsurance_cash_received", fmt),
        net_item("CFO-I23-PLEDGE", "保单质押贷款净增加额", "policy_pledge_loan_cash_paid", "policy_pledge_loan_cash_received", fmt),
        net_item("CFO-I23-LENT", "拆出资金净增加额", "funds_lent_cash_paid", "funds_lent_cash_received", fmt),
        gross_item("CFO-I23-FEE", "支付手续费及佣金的现金", "financial_fee_cash_paid", fmt),
        *employee_tax_other("CFO-I23", fmt),
    ]
    items = [*inflow, subtotal("CFO-IN", "经营活动现金流入小计", "operating", [item["item_id"] for item in inflow], fmt), *outflow]
    items.extend([
        subtotal("CFO-OUT", "经营活动现金流出小计", "operating", [item["item_id"] for item in outflow], fmt),
        subtotal("CFO-NET", "经营活动产生的现金流量净额", "operating", ["CFO-IN", "CFO-OUT"], fmt, [1, -1]),
    ])
    investing_in = [copy.deepcopy(by_id[item_id]) for item_id in ("CFI-01", "CFI-02", "CFI-03", "CFI-05")]
    investing_in[1]["name"] = "取得投资收益和利息收入收到的现金"
    investing_out = [
        copy.deepcopy(by_id["CFI-07"]),
        net_item("CFI-I23-REVERSE-REPO", "返售业务资金净增加额", "reverse_repo_cash_paid", "reverse_repo_cash_received", fmt, section="investing"),
        copy.deepcopy(by_id["CFI-06"]),
        copy.deepcopy(by_id["CFI-09"]),
    ]
    items.extend([*investing_in, subtotal("CFI-IN", "投资活动现金流入小计", "investing", [item["item_id"] for item in investing_in], fmt), *investing_out])
    items.extend([
        subtotal("CFI-OUT", "投资活动现金流出小计", "investing", [item["item_id"] for item in investing_out], fmt),
        subtotal("CFI-NET", "投资活动产生的现金流量净额", "investing", ["CFI-IN", "CFI-OUT"], fmt, [1, -1]),
    ])
    financing_in = financing_items(by_id, fmt, insurance_repo=True)
    financing_out = [copy.deepcopy(by_id[item_id]) for item_id in ("CFF-04", "CFF-05", "CFF-06")]
    items.extend([*financing_in, subtotal("CFF-IN", "筹资活动现金流入小计", "financing", [item["item_id"] for item in financing_in], fmt), *financing_out])
    items.extend([
        subtotal("CFF-OUT", "筹资活动现金流出小计", "financing", [item["item_id"] for item in financing_out], fmt),
        subtotal("CFF-NET", "筹资活动产生的现金流量净额", "financing", ["CFF-IN", "CFF-OUT"], fmt, [1, -1]),
        *(copy.deepcopy(by_id[item_id]) for item_id in ("FX", "NET-CASH", "OPENING-CASH", "CLOSING-CASH")),
    ])
    for order, item in enumerate(items, 1):
        item["display_order"] = order * 10
        item["verification_record_id"] = f"FV-INSURANCE-2023-{item['item_id']}"
    result["version"] = "1.1.0"
    result["items"] = items
    result["statement_template"] = [{key: item[key] for key in ("item_id", "name", "section", "display_order")} for item in items]
    return result


def format_record(verification_id: str, industry: str, source: str) -> dict:
    return {
        "verification_id": verification_id,
        "enterprise_type": "insurance" if industry == "insurance_2023" else industry,
        "cashflow_item_id": "FINANCIAL-FORMAT-OVERLAY",
        "candidate_formula": {"source_locator": source, "components": ["按正式格式及现金实际收付方向设置固定项目"]},
        "evidence": {
            "knowledge_base": [{"locator": "知识库_现金流量表相关内容汇编.md"}],
            "pdf_article": [{"locator": "chapter23.pdf"}],
            "second_slides": [{"locator": "现金流量表编制与复核技巧-谢海林.pptx"}],
            "second_workbook": [{"locator": "现金流量表编制与复核技巧-谢海林.xlsx"}],
            "first_workbook": [{"locator": "00-现金流案例.xlsx"}],
            "official": [{"locator": source}],
        },
        "issues": [],
        "corrected_formula": {
            "components": ["正式格式固定行及相应现金收付事实"],
            "gross_or_net": "not_applicable",
            "noncash_exclusions": ["非现金结算、公允价值变动和应计未收未付"],
            "special_adjustments": [
                "净增加额按现金流入减现金流出或现金流出减现金流入计算",
                "收到与支付方向分别单列时，仅在本方向净额为正时列示，反方向由对应项目承接",
            ],
            "restricted_cash_treatment": ["受限资金不计入现金及现金等价物"],
        },
        "netting_basis": "同一业务资产或负债的现金增加额与现金减少额轧差",
        "mutual_exclusion": "行业经营项目与通用投资项目按企业类型标签互斥",
        "conclusion": "verified",
        "reviewed_at": "2026-08-02",
    }


def insurance_2023_records(pack: dict) -> list[dict]:
    """为现行保险现金流量表的每个项目保留独立核验轨迹。"""
    source = "财政部财会〔2022〕37号保险公司财务报表格式"
    records = []
    for item in pack["items"]:
        record = format_record(
            item["verification_record_id"],
            "insurance_2023",
            source,
        )
        record["cashflow_item_id"] = item["item_id"]
        record["candidate_formula"]["components"] = [item["name"]]
        record["corrected_formula"]["components"] = [
            component["component_id"] for component in item["components"]
        ]
        record["reviewed_at"] = "2026-08-03"
        records.append(record)
    return records


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for industry in FINANCIAL_TYPES:
        raw = json.loads((args.source / f"rules/{industry}_v1.json").read_text(encoding="utf-8-sig"))
        write_json(args.output / f"rules/{industry}_v1.json", rebuild_pack(raw, industry))
        source_registry = args.source / f"references/公式核验/{industry}_v1.jsonl"
        records = [json.loads(line) for line in source_registry.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        verification_id = f"FV-{industry.upper()}-FORMAT-2018-R2"
        if not any(record["verification_id"] == verification_id for record in records):
            records.append(format_record(verification_id, industry, "财政部2018年度金融企业财务报表格式"))
        target = args.output / f"references/公式核验/{industry}_v1.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8-sig")
    insurance = json.loads((args.source / "rules/insurance_v1.json").read_text(encoding="utf-8-sig"))
    current_insurance_pack = insurance_2023_pack(insurance)
    write_json(args.output / "rules/insurance_2023_v1.json", current_insurance_pack)
    records = insurance_2023_records(current_insurance_pack)
    target = args.output / "references/公式核验/insurance_2023_v1.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8-sig",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
