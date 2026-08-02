"""把科目余额表汇总为账面资产负债表和利润表。"""

from dataclasses import dataclass

from .contracts import AccountBalance


class StatementMappingError(ValueError):
    """输入科目无法唯一映射到报表项目，修正映射后可以重试。"""


@dataclass(frozen=True)
class MappingRule:
    report_item: str
    statement: str
    amount_mode: str
    account_code_prefixes: tuple[str, ...] = ()
    account_name_contains: tuple[str, ...] = ()
    account_name_equals: tuple[str, ...] = ()

    def matches(self, row: AccountBalance) -> bool:
        code_match = any(
            row.account_code.startswith(prefix)
            for prefix in self.account_code_prefixes
        )
        name_match = any(
            term in row.account_name
            for term in self.account_name_contains
        )
        exact_name_match = row.account_name.strip() in {
            name.strip() for name in self.account_name_equals
        }
        return code_match or name_match or exact_name_match


@dataclass(frozen=True)
class StatementMapping:
    rules: tuple[MappingRule, ...]

    def unique_match(self, row: AccountBalance) -> MappingRule | None:
        matches = [rule for rule in self.rules if rule.matches(row)]
        exact_matches = [
            rule for rule in matches
            if row.account_name.strip() in {name.strip() for name in rule.account_name_equals}
        ]
        if exact_matches:
            matches = exact_matches
        elif len(matches) > 1:
            specificity = {
                id(rule): max(
                    (len(term) for term in rule.account_name_contains if term in row.account_name),
                    default=0,
                )
                for rule in matches
            }
            max_specificity = max(specificity.values())
            if max_specificity:
                matches = [rule for rule in matches if specificity[id(rule)] == max_specificity]
        unique_targets = {
            (rule.report_item, rule.statement, rule.amount_mode)
            for rule in matches
        }
        if len(unique_targets) > 1:
            names = "、".join(rule.report_item for rule in matches)
            raise StatementMappingError(
                f"科目映射不唯一：{row.account_name} -> {names}"
            )
        return matches[0] if matches else None


CONTROL_STATEMENT_ITEMS = {
    "营业总收入",
    "营业总成本",
    "营业利润",
    "利润总额",
    "净利润",
    "持续经营净利润",
    "终止经营净利润",
    "综合收益总额",
}


STANDARD_BALANCE_SHEET_ROLLUPS = {
    "货币资金": (
        "库存现金", "银行存款", "其他货币资金",
    ),
    "存货": (
        "材料采购", "在途物资", "原材料", "材料成本差异", "库存商品",
        "发出商品", "商品进销差价", "委托加工物资", "周转材料", "生产成本",
        "在产品", "半成品",
    ),
    "现金及存放中央银行款项": (
        "库存现金", "存放中央银行", "法定准备金", "超额存款准备金",
    ),
    "发放贷款和垫款": (
        "公司贷款", "个人贷款", "客户贷款", "贷款及垫款", "发放贷款", "垫款",
    ),
    "吸收存款": (
        "客户存款", "单位存款", "个人存款", "储蓄存款", "吸收存款",
    ),
    "结算备付金": ("结算备付金", "客户备付金"),
    "融出资金": ("融出资金",),
    "代理买卖证券款": ("代理买卖证券款", "客户交易结算资金"),
    "保险合同资产": ("保险合同资产", "保险获取现金流量资产"),
    "保险合同负债": ("保险合同负债", "未到期责任负债", "已发生赔款负债"),
    "分出再保险合同资产": ("分出再保险合同资产", "分保摊回未到期责任资产", "分保摊回已发生赔款资产"),
    "分出再保险合同负债": ("分出再保险合同负债",),
}


def is_control_statement_item(item_name: str) -> bool:
    """识别不能再次当作账表调整对象的小计和控制行。"""
    normalized = "".join(str(item_name).split()).rstrip("：:")
    return (
        normalized in CONTROL_STATEMENT_ITEMS
        or normalized.endswith(("合计", "总计", "小计"))
    )


def with_exact_statement_names(
    mapping: StatementMapping,
    balance_sheet_names: tuple[str, ...] | list[str],
    income_statement_names: tuple[str, ...] | list[str],
) -> StatementMapping:
    """用审定报表的明细项目补足同名科目的确定性映射。"""

    def income_mode(item_name: str) -> str:
        expense_terms = ("成本", "费用", "支出", "损失", "税金")
        return (
            "debit_minus_credit"
            if any(term in item_name for term in expense_terms)
            else "credit_minus_debit"
        )

    additions = []
    for item_name in balance_sheet_names:
        if not is_control_statement_item(item_name):
            additions.append(MappingRule(
                report_item=item_name,
                statement="balance_sheet",
                amount_mode="balance",
                account_name_contains=(item_name,),
                account_name_equals=(item_name,),
            ))
    for item_name in income_statement_names:
        if not is_control_statement_item(item_name):
            additions.append(MappingRule(
                report_item=item_name,
                statement="income_statement",
                amount_mode=income_mode(item_name),
                account_name_contains=(item_name,),
                account_name_equals=(item_name,),
            ))
    available_balance_items = {
        str(item_name).strip()
        for item_name in balance_sheet_names
        if not is_control_statement_item(item_name)
    }
    for report_item, account_names in STANDARD_BALANCE_SHEET_ROLLUPS.items():
        if report_item not in available_balance_items:
            continue
        additions.append(MappingRule(
            report_item=report_item,
            statement="balance_sheet",
            amount_mode="balance",
            account_name_contains=account_names,
            account_name_equals=account_names,
        ))
    return StatementMapping(mapping.rules + tuple(additions))


@dataclass(frozen=True)
class BookStatementLine:
    current_minor: int = 0
    prior_minor: int = 0

    def add(self, current_minor: int, prior_minor: int) -> "BookStatementLine":
        return BookStatementLine(
            current_minor=self.current_minor + current_minor,
            prior_minor=self.prior_minor + prior_minor,
        )


@dataclass(frozen=True)
class BookStatements:
    balance_sheet: dict[str, BookStatementLine]
    income_statement: dict[str, BookStatementLine]
    unmapped_accounts: tuple[AccountBalance, ...]
    unmapped_amount_minor: int


def _amounts(
    row: AccountBalance,
    amount_mode: str,
) -> tuple[int, int]:
    if amount_mode == "balance":
        return row.closing_balance_minor, row.opening_balance_minor
    if amount_mode == "debit_minus_credit":
        return row.debit_turnover_minor - row.credit_turnover_minor, 0
    if amount_mode == "credit_minus_debit":
        return row.credit_turnover_minor - row.debit_turnover_minor, 0
    raise ValueError(f"不支持的账面报表取数方式：{amount_mode}")


def build_book_statements(
    trial_balance: tuple[AccountBalance, ...] | list[AccountBalance],
    mapping: StatementMapping,
) -> BookStatements:
    balance_sheet: dict[str, BookStatementLine] = {}
    income_statement: dict[str, BookStatementLine] = {}
    unmapped = []
    for row in trial_balance:
        rule = mapping.unique_match(row)
        if rule is None:
            unmapped.append(row)
            continue
        target = balance_sheet if rule.statement == "balance_sheet" else income_statement
        current, prior = _amounts(row, rule.amount_mode)
        target[rule.report_item] = target.get(
            rule.report_item, BookStatementLine()
        ).add(current, prior)
    return BookStatements(
        balance_sheet=balance_sheet,
        income_statement=income_statement,
        unmapped_accounts=tuple(unmapped),
        unmapped_amount_minor=sum(
            abs(row.closing_balance_minor) for row in unmapped
        ),
    )
