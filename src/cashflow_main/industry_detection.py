"""根据报表项目和科目特征识别企业类型。"""

import re
from dataclasses import dataclass

from .contracts import EnterpriseType, NormalizedInputBundle

INDUSTRY_TERMS = {
    EnterpriseType.GENERAL: {
        "营业收入": 3,
        "存货": 3,
        "应收账款": 2,
        "固定资产": 2,
    },
    EnterpriseType.BANK: {
        "客户贷款及垫款": 5,
        "发放贷款和垫款": 5,
        "吸收存款及同业存放": 5,
        "同业及其他金融机构存放款项": 5,
        "存放中央银行款项": 4,
        "向中央银行借款": 4,
    },
    EnterpriseType.SECURITIES: {
        "融出资金": 5,
        "代理买卖证券款": 5,
        "买入返售金融资产": 3,
        "结算备付金": 5,
        "存出保证金": 4,
    },
    EnterpriseType.INSURANCE: {
        "保险合同负债": 5,
        "保费收入": 5,
        "应付赔付款": 4,
        "应收保费": 4,
        "赔付支出": 5,
    },
    EnterpriseType.OTHER_FINANCIAL: {
        "应收融资租赁款": 5,
        "长期应收款": 4,
        "融资租赁收入": 5,
        "未实现融资收益": 4,
    },
}


@dataclass(frozen=True)
class IndustryCandidate:
    enterprise_type: EnterpriseType
    score: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class IndustryDetection:
    preferred: EnterpriseType
    candidates: tuple[IndustryCandidate, ...]
    requires_confirmation: bool


def detect_enterprise_type(bundle: NormalizedInputBundle) -> IndustryDetection:
    names = {
        line.item_name
        for line in bundle.audited_balance_sheet + bundle.audited_income_statement
    }
    names.update(row.account_name for row in bundle.trial_balance)
    candidates = []
    for enterprise_type, weighted_terms in INDUSTRY_TERMS.items():
        evidence = tuple(
            term
            for term in weighted_terms
            if any(term in name for name in names)
        )
        candidates.append(
            IndustryCandidate(
                enterprise_type=enterprise_type,
                score=sum(weighted_terms[term] for term in evidence),
                evidence=evidence,
            )
        )
    ranked = tuple(
        sorted(
            candidates,
            key=lambda item: (-item.score, list(EnterpriseType).index(item.enterprise_type)),
        )
    )
    top, second = ranked[:2]
    return IndustryDetection(
        preferred=top.enterprise_type,
        candidates=ranked,
        requires_confirmation=top.score < 8 or top.score - second.score < 3,
    )


def detect_insurance_format(
    bundle: NormalizedInputBundle,
    period: str = "",
) -> str | None:
    """根据保险合同准则特征判断2018旧格式或2023新格式。"""
    names = {
        line.item_name
        for line in bundle.audited_balance_sheet + bundle.audited_income_statement
    }
    names.update(row.account_name for row in bundle.trial_balance)
    new_terms = (
        "保险合同资产",
        "保险合同负债",
        "分出再保险合同资产",
        "分出再保险合同负债",
        "保险服务收入",
        "保险服务费用",
    )
    old_terms = (
        "未到期责任准备金",
        "未决赔款准备金",
        "寿险责任准备金",
        "保险业务收入",
        "已赚保费",
    )
    has_new = any(any(term in name for term in new_terms) for name in names)
    has_old = any(any(term in name for term in old_terms) for name in names)
    if has_new != has_old:
        return "insurance_2023" if has_new else "insurance_2018"
    year_match = re.search(r"(?:19|20)\d{2}", period or "")
    if year_match:
        year = int(year_match.group())
        if year <= 2022:
            return "insurance_2018"
        if year >= 2026:
            return "insurance_2023"
    return None
