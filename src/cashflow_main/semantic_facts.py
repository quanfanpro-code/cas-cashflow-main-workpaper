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
    candidate_tags: tuple[str, ...] = ()
    preferred_tag: str | None = None
    strong_conflict: bool = False
    is_cash_pair: bool = False


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


def _context(pair: JournalPair) -> str:
    return _name(" ".join(
        str(value)
        for value in pair.original_fields.values()
        if value not in (None, "")
    ))


def classify_pair(
    pair: JournalPair,
    enterprise_type: EnterpriseType,
) -> PairClassification:
    financial = enterprise_type is not EnterpriseType.GENERAL
    debit = _name(pair.debit_account_name)
    credit = _name(pair.credit_account_name)
    context = _context(pair)
    cash_terms = ("库存现金", "银行存款", "其他货币资金", "现金等价物", "结算备付金")
    debit_cash = _has(debit, *cash_terms)
    credit_cash = _has(credit, *cash_terms)
    tags: list[str] = []
    evidence: list[str] = []

    def add(tag: str, reason: str) -> None:
        if tag not in tags:
            tags.append(tag)
            evidence.append(reason)

    if debit_cash and credit_cash:
        return PairClassification(
            tags=("cash_account_transfer",),
            evidence=("借贷双方均为现金或现金等价物账户，属于内部划转",),
            supplied_tags=_supplied(pair),
            is_cash_pair=True,
        )

    if (
        _has(debit, "应交税费")
        and _has(debit, "进项税")
        and not _has(context, "购建", "工程", "设备", "固定资产", "无形资产", "长期资产")
    ):
        add("purchase_input_tax", "借方为增值税进项税额")
    if _has(credit, "应交税费") and _has(credit, "销项税"):
        add("sales_output_tax", "贷方为增值税销项税额")
    if _has(debit, "坏账准备") and _has(credit, "应收账款", "应收票据"):
        add("receivable_write_off", "借记坏账准备、贷记应收款项，属于非现金核销")
    if _has(debit, "信用减值损失", "资产减值损失") and _has(credit, "坏账准备"):
        add("bad_debt_accrual", "借记减值损失、贷记坏账准备，属于非现金计提")

    if debit_cash:
        if _has(credit, "汇兑损益", "财务费用") and _has(context, "汇率变动", "期末调汇", "外币折算"):
            add("cash_exchange_effect", "外币现金因汇率变动产生折算影响")
        elif _has(context, "处置子公司", "处置营业单位") and _has(context, "以前期间", "前期"):
            add("prior_business_disposal_cash_received", "收到以前期间处置子公司或营业单位的现金")
        elif _has(context, "处置子公司", "处置营业单位"):
            add("business_disposal_cash_received", "收到处置子公司或营业单位的现金价款")
        elif _has(context, "取得子公司", "购买子公司", "企业合并") and _has(context, "取得现金", "并入现金"):
            add("acquired_business_cash_and_equivalents", "取得子公司时一并取得其现金及现金等价物")
        elif _has(credit, "固定资产清理") or (
            _has(context, "处置", "出售", "变卖")
            and _has(credit, "资产处置收益", "营业外收入")
            and _has(context, "固定资产", "无形资产", "长期资产")
        ):
            add("long_lived_asset_disposal_cash", "收到处置长期资产现金")
        elif _has(context, "保险赔款", "保险赔偿") and _has(
            context, "固定资产", "无形资产", "长期资产", "自然灾害"
        ):
            add("long_lived_asset_damage_insurance_receipt", "收到长期资产毁损保险赔款")
        elif _has(context, "政府补助", "财政补助", "稳岗补贴", "财政拨款"):
            add("government_grant_receipt", "摘要表明收到政府补助")
        elif _has(context, "退税", "税费返还", "税收返还"):
            add("cash_tax_refund", "摘要表明收到税费返还")
        elif _has(context, "罚款收入", "违约金收入", "赔偿收入"):
            add("fine_or_compensation_receipt", "摘要表明收到罚款、违约金或赔偿款")
        elif _has(context, "租金", "经营租赁") and _has(
            credit, "租赁收入", "其他业务收入", "应收账款", "其他应收款"
        ):
            add("operating_lease_receipt", "收到经营租赁租金")
        elif _has(context, "已核销", "坏账收回", "核销款收回"):
            add("recovered_written_off_receivable", "收到以前已核销的应收款")
        elif _has(credit, "短期借款"):
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
        elif _has(credit, "财务费用", "利息收入") and _has(context, "存款利息", "银行利息"):
            add("operating_interest_receipt", "收到银行存款利息")
        elif _has(
            credit,
            "营业收入", "主营业务收入", "其他业务收入", "销售收入",
            "应收账款", "应收票据", "合同资产", "预收账款", "合同负债",
        ):
            add("workpaper_formula_covered", "销售收款已由收入及经营往来变动公式覆盖")

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
        if _has(debit, "汇兑损益", "财务费用") and _has(context, "汇率变动", "期末调汇", "外币折算"):
            add("cash_exchange_effect", "外币现金因汇率变动产生折算影响")
        elif _has(context, "处置子公司", "处置营业单位") and _has(context, "转出现金", "处置单位持有现金"):
            add("disposed_business_cash_and_equivalents", "处置子公司时转出其持有的现金及现金等价物")
        elif _has(context, "取得子公司", "购买子公司", "企业合并") and _has(context, "以前期间", "前期"):
            add("prior_business_acquisition_cash_paid", "支付以前期间取得子公司或营业单位的现金价款")
        elif _has(context, "取得子公司", "购买子公司", "企业合并"):
            add("business_acquisition_cash_paid", "支付取得子公司或营业单位的现金价款")
        elif _has(context, "投资交易手续费", "投资手续费", "投资佣金"):
            add("investment_transaction_cost_cash", "支付投资交易相关手续费或佣金")
        elif _has(context, "资本化利息", "资本化借款利息", "借款费用资本化"):
            add("capitalized_interest_cash", "支付应计入长期资产成本的资本化利息")
            add("interest_cash_paid", "资本化利息仍属于偿付利息现金流")
        elif _has(debit, "固定资产清理"):
            if _has(context, "税", "税费"):
                add("long_lived_asset_disposal_tax_cash", "支付长期资产处置相关税费")
            else:
                add("long_lived_asset_disposal_cost_cash", "支付长期资产处置清理费用")
        elif _has(debit, "短期借款", "长期借款", "应付债券"):
            add("debt_principal_cash_repaid", "借记债务本金、贷记现金")
        elif financial and _has(debit, "保户储金及投资款"):
            add("policyholder_deposit_cash_paid", "保险公司保户储金及投资款支付现金")
        elif financial and _has(debit, "融出资金"):
            add("margin_financing_cash_paid", "证券公司融出资金支付现金")
        elif financial and _has(debit, "交易性金融资产"):
            add("trading_asset_cash_paid", "金融企业买入交易目的金融资产支付现金")
        elif _has(debit, "投资性房地产"):
            add("long_lived_asset_cash_addition", "购建投资性房地产支付现金")
        elif _has(debit, "长期股权投资", "交易性金融资产", "债权投资", "其他债权投资", "投资"):
            add("investment_acquisition_cash", "借记投资资产、贷记现金")
        elif _has(debit, "固定资产", "在建工程", "无形资产", "开发支出", "长期待摊"):
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
            if financial:
                add("other_financing_cash_paid", "金融企业支付债券发行费用，列入其他筹资流出")
            else:
                add("bond_issue_cost_cash_paid", "一般企业支付债券发行费用，从发行债券收款中扣除")
        elif _has(debit, "发行费用", "承销费"):
            add("financing_issue_cost_cash_paid", "发行费用未注明股权或债券性质")
            add("other_financing_cash_paid", "性质不明的发行费用暂列其他筹资活动现金流出")
        elif _has(debit, "应交税费") and _has(
            context, "购建", "工程", "设备", "固定资产", "无形资产", "长期资产"
        ):
            add("long_lived_asset_input_tax_cash", "支付长期资产购建相关税费")
        elif _has(debit, "应交税费"):
            add("operating_tax_cash_paid", "借记应交税费、贷记现金")
        elif _has(debit, "预付工程款", "预付设备款") or (
            _has(debit, "预付账款") and _has(context, "工程", "设备", "长期资产")
        ):
            add("capex_prepayment_change", "支付工程或设备预付款，转入长期资产购建现金流")
            add("long_lived_asset_cash_addition", "支付工程或设备预付款，属于长期资产购建现金流")
        elif _has(
            debit,
            "营业成本", "主营业务成本", "其他业务成本", "存货", "原材料", "库存商品",
            "应付账款", "应付票据", "预付账款", "生产成本",
            "销售费用", "管理费用", "研发费用", "应付职工薪酬",
        ):
            add("workpaper_formula_covered", "采购、费用或职工付款已由利润表及经营往来变动公式覆盖")

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

    if _has(credit, "应付职工薪酬") and _has(
        debit, "管理费用", "销售费用", "研发费用", "营业成本", "生产成本", "制造费用", "在建工程", "开发支出"
    ):
        add("employee_compensation_expense", "借记成本费用或长期资产、贷记应付职工薪酬")
    if _has(debit, "在建工程", "开发支出") and _has(credit, "应付职工薪酬"):
        add("capex_employee_cash", "工程或开发人员薪酬应从经营职工现金中转入投资活动")
    if _has(debit, "应付职工薪酬") and not credit_cash and _has(
        credit, "存货", "库存商品", "固定资产", "无形资产"
    ):
        add("noncash_employee_benefit", "以非现金资产发放职工福利")
    if _has(debit, "固定资产", "在建工程", "无形资产", "投资性房地产", "开发支出") and _has(
        credit, "应付账款", "应付票据", "长期应付款"
    ):
        add("capex_payable_change", "长期资产形成的应付款不属于经营采购往来")

    if _has(debit, "使用权资产") and _has(credit, "租赁负债"):
        add("right_of_use_asset_noncash_addition", "使用权资产和租赁负债初始确认不涉及现金")
    if _has(debit, "存货", "原材料", "库存商品", "生产成本") and _has(credit, "应付职工薪酬"):
        add("inventory_employee_cost", "计入存货或生产成本的职工薪酬未直接付现")
    if _has(debit, "营业成本", "生产成本", "制造费用") and _has(credit, "累计折旧", "累计摊销"):
        add("inventory_depreciation_cost", "成本中包含不涉及现金的折旧或摊销")
    if _has(debit, "固定资产", "在建工程", "无形资产", "长期股权投资") and _has(
        credit, "存货", "原材料", "库存商品"
    ):
        add("inventory_to_capex_or_investment", "存货转作长期资产或投资，不涉及现金")
    if _has(debit, "存货", "原材料", "库存商品", "生产成本") and _has(
        credit, "实收资本", "股本", "资本公积", "长期应付款", "固定资产"
    ):
        add("inventory_noncash_increase", "投资投入或非货币结算形成存货增加")
    if _has(debit, "应付账款", "应付票据") and _has(credit, "应收账款", "应收票据"):
        add("receivable_payable_offset", "应收应付直接抵销，不涉及现金")
    if _has(credit, "应收账款", "应收票据", "合同资产") and not debit_cash and _has(
        debit, "固定资产", "无形资产", "存货", "长期股权投资", "其他应收款"
    ) and not (
        _has(debit, "其他应收款", "长期应收款")
        and _has(context, "非经营", "往来")
    ):
        add("noncash_receivable_settlement", "以非现金资产或往来转换结算经营应收款")
    if _has(credit, "应收账款", "应收票据", "合同资产") and _has(
        debit, "其他应收款", "长期应收款"
    ) and _has(context, "非经营", "往来"):
        add("non_operating_receivable_change", "经营性应收转为非经营性往来")
    if _has(debit, "应付账款", "应付票据") and not credit_cash and _has(
        credit, "固定资产", "无形资产", "存货", "股本", "实收资本"
    ):
        add("payable_noncash_settlement", "以非现金资产或权益结算应付款")
    if _has(debit, "财务费用", "利息支出") and _has(credit, "应收票据") and _has(
        context, "贴现"
    ):
        add("bill_discount_interest", "应收票据贴现利息不属于销售收现")
    if _has(credit, "应收票据") and not debit_cash and _has(context, "背书"):
        add("endorsed_note", "票据背书结算不涉及现金")

    candidate_tags: tuple[str, ...] = ()
    preferred_tag = None
    strong_conflict = False
    if (debit_cash or credit_cash) and not tags:
        receipt = debit_cash
        if receipt:
            operating_tag = (
                "other_operating_receipt"
                if enterprise_type is EnterpriseType.GENERAL
                else "other_operating_cash_received"
            )
            candidates = (
                operating_tag,
                "other_investing_cash_received",
                "other_financing_cash_received",
            )
        else:
            candidates = (
                "other_operating_cash_paid",
                "other_investing_cash_paid",
                "other_financing_cash_paid",
            )
        if _has(context, "投资", "长期资产", "固定资产", "无形资产", "子公司"):
            preferred_tag = candidates[1]
            candidate_tags = (candidates[1],)
        elif _has(context, "融资", "筹资", "借款", "股东", "资本"):
            preferred_tag = candidates[2]
            candidate_tags = (candidates[2],)
        else:
            preferred_tag = candidates[0]
            candidate_tags = candidates
            strong_conflict = True
        if preferred_tag == "other_operating_receipt":
            add("other_operating_receipt", "借贷科目和摘要不能唯一证明活动类别，按经营活动首选暂编")
        elif preferred_tag == "other_operating_cash_received":
            add("other_operating_cash_received", "借贷科目和摘要不能唯一证明活动类别，按经营活动首选暂编")
        elif preferred_tag == "other_investing_cash_received":
            add("other_investing_cash_received", "借贷科目和摘要指向投资活动其他流入")
        elif preferred_tag == "other_financing_cash_received":
            add("other_financing_cash_received", "借贷科目和摘要指向筹资活动其他流入")
        elif preferred_tag == "other_operating_cash_paid":
            add("other_operating_cash_paid", "借贷科目和摘要不能唯一证明活动类别，按经营活动首选暂编")
        elif preferred_tag == "other_investing_cash_paid":
            add("other_investing_cash_paid", "借贷科目和摘要指向投资活动其他流出")
        else:
            add("other_financing_cash_paid", "借贷科目和摘要指向筹资活动其他流出")

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
        candidate_tags=candidate_tags,
        preferred_tag=preferred_tag,
        strong_conflict=strong_conflict,
        is_cash_pair=debit_cash or credit_cash,
    )
