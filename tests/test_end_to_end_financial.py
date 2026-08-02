import json
from pathlib import Path

import pytest

from cashflow_main.rule_loader import load_rule_pack

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("industry", "special_name"),
    [("bank", "客户存款和同业存放款项净增加额"), ("securities", "代理买卖证券收到的现金净额"), ("insurance", "收到原保险合同保费取得的现金"), ("other_financial", "收取利息、手续费及佣金的现金")],
)
def test_each_financial_industry_switches_to_its_isolated_pack(industry, special_name):
    registry = {json.loads(line)["verification_id"] for line in (ROOT / f"references/公式核验/{industry}_v1.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()}
    pack = load_rule_pack(ROOT / f"rules/{industry}_v1.json", registry)
    names = {item.name for item in pack.items}
    assert special_name in names
    assert "销售商品、提供劳务收到的现金" not in names if industry != "other_financial" else True
    assert all(item.verification_record_id in registry for item in pack.items)
