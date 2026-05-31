from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from html import unescape
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS_ROOT = PROJECT_ROOT / "reports"


def cell_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_number(value: str) -> float:
    clean = value.replace(" ", "").replace("%", "")
    match = re.search(r"-?\d+(?:\.\d+)?", clean)
    if not match:
        return 0.0
    return float(match.group(0))


def parse_rows(html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        cells = [
            cell_text(cell.group(1))
            for cell in re.finditer(r"<t[dh][^>]*>(.*?)</t[dh]>", row_match.group(1), flags=re.S | re.I)
        ]
        if cells:
            rows.append(cells)
    return rows


def parse_summary(rows: list[list[str]]) -> dict[str, str]:
    summary: dict[str, str] = {}
    wanted = {
        "专家",
        "交易品种",
        "期间",
        "初始入金",
        "杠杆",
        "质量历史",
        "总净盈利",
        "毛利",
        "毛损",
        "最大结余亏损",
        "最大净值亏损",
        "相对结余亏损",
        "相对净值亏损",
        "盈利因子",
        "预期收益",
        "采收率",
        "夏普比率",
        "交易总计",
        "总成交",
        "盈利交易 (% 全部)",
        "亏损交易 (% 全部)",
        "最大 获利交易",
        "最大 亏损交易",
        "平均 获利交易",
        "平均 亏损交易",
        "最小持仓时间",
        "最大持仓时间",
        "平均持仓时间",
    }

    for cells in rows:
        for index, value in enumerate(cells[:-1]):
            label = value.rstrip(":")
            if label in wanted:
                summary[label] = cells[index + 1]
    return summary


def parse_deals(rows: list[list[str]]) -> list[dict[str, str | float]]:
    deals: list[dict[str, str | float]] = []
    for cells in rows:
        if len(cells) != 13:
            continue
        if cells[3] not in {"buy", "sell", "balance"}:
            continue
        if cells[3] != "balance" and cells[4] not in {"in", "out", "inout", "out by"}:
            continue

        commission = parse_number(cells[8])
        swap = parse_number(cells[9])
        profit = parse_number(cells[10])
        deals.append(
            {
                "time": cells[0],
                "deal": cells[1],
                "symbol": cells[2],
                "type": cells[3],
                "direction": cells[4],
                "volume": cells[5],
                "price": cells[6],
                "order": cells[7],
                "commission": commission,
                "swap": swap,
                "profit": profit,
                "net": commission + swap + profit,
                "balance": parse_number(cells[11]),
                "comment": cells[12],
            }
        )
    return deals


def month_key(time_text: str) -> str:
    return time_text[:7]


def analyze(report: Path, out_dir: Path) -> dict[str, object]:
    html = report.read_text(encoding="utf-16", errors="ignore")
    if "<html" not in html.lower():
        html = report.read_text(encoding="utf-8", errors="ignore")

    rows = parse_rows(html)
    summary = parse_summary(rows)
    deals = parse_deals(rows)
    closed = [deal for deal in deals if deal["direction"] == "out"]
    entries = [deal for deal in deals if deal["direction"] == "in"]

    monthly: dict[str, dict[str, float | int | str]] = defaultdict(
        lambda: {"month": "", "trades": 0, "net": 0.0, "swap": 0.0, "profit": 0.0}
    )
    for deal in closed:
        month = month_key(str(deal["time"]))
        row = monthly[month]
        row["month"] = month
        row["trades"] = int(row["trades"]) + 1
        row["net"] = float(row["net"]) + float(deal["net"])
        row["swap"] = float(row["swap"]) + float(deal["swap"])
        row["profit"] = float(row["profit"]) + float(deal["profit"])

    monthly_rows = [monthly[key] for key in sorted(monthly)]
    for row in monthly_rows:
        row["net"] = round(float(row["net"]), 2)
        row["swap"] = round(float(row["swap"]), 2)
        row["profit"] = round(float(row["profit"]), 2)

    entry_comments = Counter(str(deal["comment"]) for deal in entries)
    result = {
        "report": str(report),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "derived": {
            "deals": len(deals),
            "entries": len(entries),
            "closed_trades": len(closed),
            "net_from_closed_deals": round(sum(float(deal["net"]) for deal in closed), 2),
            "swap_from_closed_deals": round(sum(float(deal["swap"]) for deal in closed), 2),
            "profit_before_swap": round(sum(float(deal["profit"]) for deal in closed), 2),
            "entry_comments": dict(entry_comments),
        },
        "monthly": monthly_rows,
        "largest_losses": sorted(closed, key=lambda row: float(row["net"]))[:10],
        "largest_wins": sorted(closed, key=lambda row: float(row["net"]), reverse=True)[:10],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    if monthly_rows:
        with (out_dir / "monthly.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["month", "trades", "net", "swap", "profit"])
            writer.writeheader()
            writer.writerows(monthly_rows)

    if deals:
        with (out_dir / "deals.csv").open("w", newline="", encoding="utf-8") as file:
            fieldnames = list(deals[0].keys())
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(deals)

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse an MT5 Strategy Tester HTML report.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--out-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = args.report.resolve()
    out_dir = args.out_dir
    if out_dir is None:
        out_dir = args.reports_root / f"mt5_report_{report.stem}"

    result = analyze(report, out_dir.resolve())
    summary = result["summary"]
    derived = result["derived"]

    print(f"reports:       {out_dir.resolve()}")
    print(f"period:        {summary.get('期间', '')}")
    print(f"net_profit:    {summary.get('总净盈利', '')}")
    print(f"profit_factor: {summary.get('盈利因子', '')}")
    print(f"sharpe:        {summary.get('夏普比率', '')}")
    print(f"max_dd_equity: {summary.get('最大净值亏损', '')}")
    print(f"trades:        {summary.get('交易总计', '')}")
    print(f"closed_trades: {derived['closed_trades']}")
    print(f"net_closed:    {derived['net_from_closed_deals']}")
    print(f"swap_closed:   {derived['swap_from_closed_deals']}")


if __name__ == "__main__":
    main()
