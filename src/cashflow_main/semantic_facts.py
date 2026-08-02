"""从已拆分的一借一贷关系生成工作底稿所需的补充业务事实。"""

import re
from dataclasses import dataclass

from .contracts import EnterpriseType, JournalPair


@dataclass(frozen=True)
class PairClassification:
    tags: tuple[str, ...]
    evidence: tuple[str, ...]
    supplied_tags: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


def _name(value: str) -> str:
    return re.sub(r"[\s_—－-]+", "", value or "").lower()


def _has(value: str, *terms: str) -> bool:
    return any(_name(term) in value for term in terms)


def _supplied(pair: JournalPair) -> tuple[str, ...]:
    raw = pair.original_fields.get("现金流标签") or pair.original_fields.get("现流标签")
    if not raw:
        return ()
    return tuple(
        value.strip()
        for value in str(raw).replace("；", ",").split(",")
        if value.strip()
    )


def classify_pair(
    pair: JournalPair,
    enterprise_type: EnterpriseType,
) -> PairClassification:
    financial = enterprise_type is not EnterpriseType.GENERAL
    debit = _name(pair.debit_account_name)
    credit = _name(pair.credit_account_name)
    cash_terms = ("库存现金", "银行存款", "其他货币资金", "现金等价物", "结算备付金")
    debit_cash = _has(debit, *cash_terms)
    credit_cash = _has(credit, *cash_terms)
    tags: list[str] = []
    evidence: list[str] = []

    def add(tag: str, reason: str) -> None:
        if tag not in tags:
            tags.append(tag)
            evidence.append(reason)

    if _has(debit, "应交税费") and _has(debit, "进项税"):
        add("purchase_input_tax", "借方为增值税进项税额")
    if _has(credit, "应交税费") and _has(credit, "销项税"):
        add("sales_output_tax", "贷方为增值税销项税额")
    if _has(debit, "坏账准备") and _has(credit, "应收账款", "应收票据"):
        add("receivable_write_off", "借记坏账准备、贷记应收款项，属于非现金核销")
    if _has(debit, "信用减值损失", "资产减值损失") and _has(credit, "坏账准备"):
        add("bad_debt_accrual", "借记减值损失、贷记坏账准备，属于非现金计提")

    if debit_cash:
        if _has(credit, "短期借款"):
            add("short_term_borrowing_cash_received", "借记现金、贷记短期借款")
        elif _has(credit, "长期借款"):
            add("long_term_borrowing_cash_received", "借记现金、贷记长期借款")
        elif _has(credit, "应付债券"):
            add("bond_issue_cash_received", "借记现金、贷记应付债券")
        elif _has(credit, "实收资本", "股本", "其他权益工具", "资本公积"):
            add("equity_investment_cash_received", "借记现金、贷记权益科目")
        elif financial and _has(credit, "交易性金融资产"):
            add("trading_asset_cash_received", "金融企业出售交易目的金融资产收到现金")
        elif _has(credit, "应收股利"):
            add("cash_dividend_received", "借记现金、贷记应收股利")
        elif _has(credit, "应收利息"):
            add("investment_interest_received", "借记现金、贷记应收利息")
        elif _has(credit, "投资收益"):
            add("investment_interest_received", "借记现金、贷记投资收益")
        elif _has(credit, "处置子公司", "处置营业单位", "子公司股权"):
            add("business_disposal_cash_received", "借记现金、贷记处置子公司或营业单位相关科目")
        elif _has(credit, "长期股权投资", "交易性金融资产", "债权投资", "其他债权投资", "投资资产"):
            add("investment_principal_recovered", "借记现金、贷记投资资产")

        if financial:
            if _has(credit, "吸收存款", "客户存款", "同业存放"):
                add("customer_deposit_cash_received", "金融企业收到客户存款或同业存放现金")
            elif _has(credit, "融出资金"):
                add("margin_financing_cash_received", "证券公司收回融出资金收到现金")
            elif _has(credit, "保户储金及投资款"):
                add("policyholder_deposit_cash_received", "保险公司保户储金及投资款收到现金")
            elif _has(credit, "保单质押贷款", "质押贷款"):
                add("policy_pledge_loan_cash_received", "收回保单质押贷款收到现金")
            elif _has(credit, "分出再保险合同", "应收分保账款"):
                add("outward_reinsurance_cash_received", "收到分出再保险业务现金")
                add("reinsurance_cash_received", "收到再保险业务现金")
            elif _has(credit, "分入再保险合同", "应付分保账款"):
                add("inward_reinsurance_cash_received", "收到分入再保险业务现金")
                add("reinsurance_cash_received", "收到再保险业务现金")
            elif _has(credit, "客户贷款及垫款", "发放贷款和垫款"):
                add("customer_loan_cash_received", "收回客户贷款及垫款收到现金")
            elif _has(credit, "存放中央银行", "存放同业"):
                add("central_interbank_deposit_cash_received", "收回存放中央银行或同业款项收到现金")
            elif _has(credit, "向中央银行借款"):
                add("central_bank_borrowing_cash_received", "金融企业收到中央银行借款")
            elif _has(credit, "向其他金融机构拆入"):
                add("other_financial_borrowing_cash_received", "金融企业向其他金融机构拆入现金")
            elif _has(credit, "拆入资金"):
                add("interbank_borrowing_cash_received", "金融企业拆入资金收到现金")
            elif _has(credit, "卖出回购金融资产", "回购业务资金"):
                add("repo_cash_received", "卖出回购业务收到现金")
            elif _has(credit, "代理买卖证券款", "客户交易结算资金"):
                add("brokerage_funds_cash_received", "代理买卖证券收到客户现金")
            elif _has(credit, "买入返售金融资产"):
                add("reverse_repo_cash_received", "返售业务收回现金")
            elif _has(credit, "拆出资金"):
                add("funds_lent_cash_received", "拆出资金收回现金")
            elif _has(credit, "营业收入", "主营业务收入", "其他业务收入", "销售收入"):
                add("financial_sales_service_cash_received", "金融企业销售商品或提供劳务收到现金")
            elif _has(credit, "手续费及佣金收入", "利息收入"):
                add("financial_interest_fee_cash_received", "金融企业收到利息、手续费及佣金现金")
            elif _has(credit, "保险合同负债", "预收保费"):
                add("insurance_contract_premium_cash_received", "收到签发保险合同保费现金")
            elif _has(credit, "保险业务收入", "保费收入"):
                add("direct_insurance_premium_receipt", "收到原保险合同保费现金")
            elif _has(credit, "分入再保险合同"):
                add("inward_reinsurance_cash_received", "收到分入再保险合同现金")
                add("reinsurance_cash_received", "收到再保险业务现金")

    if credit_cash:
        if _has(debit, "短期借款", "长期借款", "应付债券"):
            add("debt_principal_cash_repaid", "借记债务本金、贷记现金")
        elif financial and _has(debit, "保户储金及投资款"):
            add("policyholder_deposit_cash_paid", "保险公司保户储金及投资款支付现金")
        elif financial and _has(debit, "融出资金"):
            add("margin_financing_cash_paid", "证券公司融出资金支付现金")
        elif financial and _has(debit, "交易性金融资产"):
            add("trading_asset_cash_paid", "金融企业买入交易目的金融资产支付现金")
        elif _has(debit, "长期股权投资", "交易性金融资产", "债权投资", "其他债权投资", "投资"):
            add("investment_acquisition_cash", "借记投资资产、贷记现金")
        elif _has(debit, "固定资产", "在建工程", "无形资产", "投资性房地产", "开发支出", "长期待摊"):
            add("long_lived_asset_cash_addition", "借记长期资产、贷记现金")
        elif _has(debit, "应付利息", "财务费用", "利息支出"):
            add(
                "financial_interest_fee_cash_paid" if financial else "interest_cash_paid",
                "借记利息相关科目、贷记现金",
            )
        elif _has(debit, "应付股利", "利润分配"):
            add("dividend_cash_paid", "借记股利或利润分配、贷记现金")
        elif _has(debit, "租赁负债"):
            add("lease_liability_cash_paid", "借记租赁负债、贷记现金")
        elif _has(debit, "处置子公司费用", "处置营业单位费用"):
            add("business_disposal_cost_cash_paid", "借记处置子公司或营业单位费用、贷记现金")
        elif _has(debit, "股票发行费用", "股权发行费用", "股票承销费"):
            add("equity_issue_cost_cash_paid", "支付股票或股权融资发行费用")
        elif _has(debit, "债券发行费用", "债券承销费"):
            add(
                "other_financing_cash_paid" if financial else "bond_issue_cost_cash_paid",
                "支付债券发行费用，按适用报表格式列入债券净收款或其他筹资流出",
            )
        elif _has(debit, "发行费用", "承销费"):
            add("financing_issue_cost_cash_paid", "发行费用未注明股权或债券性质")
            add("other_financing_cash_paid", "性质不明的发行费用暂列其他筹资活动现金流出")
        elif _has(debit, "应交税费"):
            add("operating_tax_cash_paid", "借记应交税费、贷记现金")

        if financial:
            if _has(debit, "吸收存款", "客户存款", "同业存放"):
                add("customer_deposit_cash_paid", "金融企业向客户或同业支付存款现金")
            elif _has(debit, "融出资金"):
                add("margin_financing_cash_paid", "证券公司融出资金支付现金")
            elif _has(debit, "保户储金及投资款"):
                add("policyholder_deposit_cash_paid", "保险公司保户储金及投资款支付现金")
            elif _has(debit, "应付保单红利", "保单红利"):
                add("policy_dividend_cash_paid", "保险公司支付保单红利现金")
            elif _has(debit, "向中央银行借款"):
                add("central_bank_borrowing_cash_repaid", "金融企业偿还中央银行借款")
            elif _has(debit, "向其他金融机构拆入"):
                add("other_financial_borrowing_cash_repaid", "金融企业偿还其他金融机构拆入资金")
            elif _has(debit, "拆入资金"):
                add("interbank_borrowing_cash_repaid", "金融企业归还拆入资金")
            elif _has(debit, "卖出回购金融资产", "回购业务资金"):
                add("repo_cash_paid", "卖出回购业务支付现金")
            elif _has(debit, "代理买卖证券款", "客户交易结算资金"):
                add("brokerage_funds_cash_paid", "代理买卖证券向客户支付现金")
            elif _has(debit, "买入返售金融资产"):
                add("reverse_repo_cash_paid", "返售业务支付现金")
            elif _has(debit, "拆出资金"):
                add("funds_lent_cash_paid", "拆出资金支付现金")
            elif _has(debit, "客户贷款及垫款", "发放贷款和垫款"):
                add("customer_loan_cash_paid", "向客户发放贷款支付现金")
            elif _has(debit, "存放中央银行", "存放同业"):
                add("central_interbank_deposit_cash_paid", "存放中央银行或同业支付现金")
            elif _has(debit, "手续费及佣金支出"):
                add("financial_interest_fee_cash_paid", "金融企业支付手续费及佣金现金")
                add("financial_fee_cash_paid", "保险公司支付手续费及佣金现金")
            elif _has(debit, "应付职工薪酬"):
                add("employee_cash_payment", "金融企业支付职工薪酬现金")
            elif _has(debit, "应交税费"):
                add("tax_cash_payment", "金融企业支付税费现金")
            elif _has(debit, "分出再保险合同", "应收分保账款"):
                add("outward_reinsurance_cash_paid", "支付分出再保险合同现金")
                add("reinsurance_cash_paid", "支付再保险业务现金")
            elif _has(debit, "分入再保险合同", "应付分保账款"):
                add("inward_reinsurance_cash_paid", "支付分入再保险合同现金")
                add("reinsurance_cash_paid", "支付再保险业务现金")
            elif _has(debit, "保险合同负债", "保险合同赔付", "赔付支出"):
                add("insurance_contract_claim_cash_paid", "支付签发保险合同赔款现金")
                add("direct_insurance_claim_cash_paid", "支付原保险合同赔付款现金")
            elif _has(debit, "保单质押贷款", "质押贷款"):
                add("policy_pledge_loan_cash_paid", "发放保单质押贷款支付现金")

    if (
        _has(debit, "固定资产", "在建工程", "无形资产", "投资性房地产", "开发支出", "长期待摊")
        and not credit_cash
        and _has(credit, "应付账款", "长期应付款", "租赁负债", "实收资本", "资本公积")
    ):
        add("noncash_long_lived_asset_addition", "长期资产增加但贷方不是现金")

    supplied = _supplied(pair)
    if tags:
        conflicts = tuple(tag for tag in supplied if tag not in tags)
        final_tags = tuple(tags + [tag for tag in supplied if tag in tags])
    else:
        conflicts = ()
        final_tags = supplied
        if supplied:
            evidence.append("仅有外部现金流标签，借贷关系未能独立证明")
    return PairClassification(
        tags=tuple(dict.fromkeys(final_tags)),
        evidence=tuple(evidence),
        supplied_tags=supplied,
        conflicts=conflicts,
    )
