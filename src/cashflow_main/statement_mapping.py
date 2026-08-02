"""把科目余额表汇总为账面资产负债表和利润表。"""

from dataclasses import dataclass

from .contracts import AccountBalance


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
            raise ValueError(f"科目映射不唯一：{row.account_name} -> {names}")
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
