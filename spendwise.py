#!/usr/bin/env python3
"""
SpendWise — a small CLI expense + subscription tracker.

Two halves in one tool:
  1. Expenses: add spending, list it, and get monthly summaries with
     per-category totals and a simple ASCII bar chart.
  2. Subscriptions: track recurring subscriptions, see your total monthly
     "burn", and get warned about renewals coming up soon.

Everything is stored in plain CSV/JSON files under ~/.spendwise/
(or $SPENDWISE_DIR if set), so your data is yours and easy to inspect.
Pure standard library — no dependencies to install.

Example:
    python3 spendwise.py add 250 food --note "lunch"
    python3 spendwise.py summary
    python3 spendwise.py sub add Netflix 199 monthly 2026-11-01
    python3 spendwise.py overview
"""

import argparse
import calendar
import csv
import json
import os
import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

CURRENCY = "₹"  # display symbol; amounts are stored as plain numbers

EXPENSE_HEADERS = ["id", "date", "amount", "category", "note"]
SUB_HEADERS = ["name", "cost", "cycle", "renewal"]


def data_dir() -> Path:
    """Where SpendWise keeps its data.

    Defaults to ~/.spendwise. Tests (and anyone who wants a custom
    location) can point it elsewhere with the SPENDWISE_DIR env var.
    """
    override = os.environ.get("SPENDWISE_DIR")
    return Path(override).expanduser() if override else Path.home() / ".spendwise"


def _path(name: str) -> Path:
    return data_dir() / name


def ensure_store() -> None:
    """Create the data directory and empty data files on first run."""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    if not _path("expenses.csv").exists():
        with open(_path("expenses.csv"), "w", newline="") as f:
            csv.writer(f).writerow(EXPENSE_HEADERS)
    if not _path("subscriptions.csv").exists():
        with open(_path("subscriptions.csv"), "w", newline="") as f:
            csv.writer(f).writerow(SUB_HEADERS)
    if not _path("budgets.json").exists():
        _path("budgets.json").write_text(json.dumps({}, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def today() -> date:
    return date.today()


def parse_date(text: str) -> date:
    """Parse an ISO date (YYYY-MM-DD); raise a friendly error otherwise."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"bad date {text!r} — use YYYY-MM-DD")


def parse_month(text: str) -> str:
    """Validate a YYYY-MM month string and return it unchanged."""
    try:
        y, m = text.split("-")
        if not (len(y) == 4 and 1 <= int(m) <= 12):
            raise ValueError
        return text
    except (ValueError, AttributeError):
        raise ValueError(f"bad month {text!r} — use YYYY-MM")


def add_months(d: date, months: int) -> date:
    """Add calendar months to a date, clamping to the end of short months
    (e.g. Jan 31 + 1 month -> Feb 28, not an error)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def next_renewal(renewal: date, cycle: str) -> date:
    """Given a renewal date and billing cycle, find the next renewal that
    has not passed yet. If the stored date is in the past (e.g. the user
    entered the last renewal instead of the next one), roll it forward
    by whole billing cycles until it lands on today or later."""
    t = today()
    while renewal < t:
        if cycle == "monthly":
            renewal = add_months(renewal, 1)
        else:  # yearly
            try:
                renewal = renewal.replace(year=renewal.year + 1)
            except ValueError:
                # Feb 29 on a non-leap year -> Feb 28
                renewal = renewal.replace(year=renewal.year + 1, day=28)
    return renewal


def fmt_money(amount: float) -> str:
    return f"{CURRENCY}{amount:,.2f}"


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------

def read_expenses() -> list[dict]:
    ensure_store()
    with open(_path("expenses.csv"), newline="") as f:
        return list(csv.DictReader(f))


def _write_expenses(rows: list[dict]) -> None:
    with open(_path("expenses.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EXPENSE_HEADERS)
        w.writeheader()
        w.writerows(rows)


def add_expense(amount: float, category: str, note: str = "", on: date | None = None) -> dict:
    """Record one expense. Returns the stored row dict.

    Raises ValueError on bad input (non-positive amount, empty category).
    Categories are normalized to lowercase so 'Food' and 'food' don't
    split into two buckets.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")
    category = category.strip().lower()
    if not category:
        raise ValueError("category must not be empty")

    rows = read_expenses()
    new_id = max((int(r["id"]) for r in rows), default=0) + 1
    row = {
        "id": str(new_id),
        "date": (on or today()).isoformat(),
        "amount": f"{amount:.2f}",
        "category": category,
        "note": note or "",
    }
    rows.append(row)
    _write_expenses(rows)
    return row


def month_expenses(month: str) -> list[dict]:
    """All expenses whose date falls in the given YYYY-MM month."""
    return [r for r in read_expenses() if r["date"].startswith(month)]


def summarize(month: str) -> tuple[float, dict[str, float]]:
    """Return (total, {category: total}) for a month, categories sorted
    by total descending."""
    totals: dict[str, float] = {}
    for r in month_expenses(month):
        totals[r["category"]] = totals.get(r["category"], 0.0) + float(r["amount"])
    totals = dict(sorted(totals.items(), key=lambda kv: kv[1], reverse=True))
    return sum(totals.values()), totals


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

def read_budgets() -> dict[str, float]:
    ensure_store()
    return {k: float(v) for k, v in json.loads(_path("budgets.json").read_text()).items()}


def set_budget(category: str, amount: float) -> None:
    if amount <= 0:
        raise ValueError("budget amount must be positive")
    category = category.strip().lower()
    budgets = read_budgets()
    budgets[category] = amount
    _path("budgets.json").write_text(json.dumps(budgets, indent=2) + "\n")


def budget_warnings(month: str) -> list[str]:
    """Check this month's spending against every budget.

    Returns warning lines for categories that are over budget or at/above
    80% of it — the 80% heads-up gives you a chance to slow down before
    you blow the budget.
    """
    _, by_cat = summarize(month)
    warnings = []
    for category, limit in read_budgets().items():
        spent = by_cat.get(category, 0.0)
        pct = (spent / limit * 100) if limit else 0
        if spent > limit:
            warnings.append(
                f"OVER BUDGET: {category} spent {fmt_money(spent)} of {fmt_money(limit)} "
                f"({pct:.0f}%)"
            )
        elif pct >= 80:
            warnings.append(
                f"close to budget: {category} spent {fmt_money(spent)} of {fmt_money(limit)} "
                f"({pct:.0f}%)"
            )
    return warnings

# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

def read_subscriptions() -> list[dict]:
    ensure_store()
    with open(_path("subscriptions.csv"), newline="") as f:
        return list(csv.DictReader(f))


def _write_subscriptions(rows: list[dict]) -> None:
    with open(_path("subscriptions.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUB_HEADERS)
        w.writeheader()
        w.writerows(rows)


def add_subscription(name: str, cost: float, cycle: str, renewal: date) -> dict:
    """Add a subscription. cycle is 'monthly' or 'yearly'.

    The renewal date is rolled forward by next_renewal() at read time,
    so a past date still resolves to the correct upcoming renewal.
    Names are unique — adding the same name twice is an error.
    """
    name = name.strip()
    if not name:
        raise ValueError("subscription name must not be empty")
    if cost <= 0:
        raise ValueError("cost must be positive")
    if cycle not in ("monthly", "yearly"):
        raise ValueError("cycle must be 'monthly' or 'yearly'")

    rows = read_subscriptions()
    if any(r["name"].lower() == name.lower() for r in rows):
        raise ValueError(f"subscription {name!r} already exists (remove it first to change it)")
    row = {"name": name, "cost": f"{cost:.2f}", "cycle": cycle, "renewal": renewal.isoformat()}
    rows.append(row)
    _write_subscriptions(rows)
    return row


def remove_subscription(name: str) -> bool:
    """Remove a subscription by name (case-insensitive). Returns True if
    something was actually removed."""
    rows = read_subscriptions()
    kept = [r for r in rows if r["name"].lower() != name.strip().lower()]
    if len(kept) == len(rows):
        return False
    _write_subscriptions(kept)
    return True


def subscription_status(row: dict) -> tuple[date, int]:
    """Resolve a stored subscription row to (next_renewal_date, days_away)."""
    nxt = next_renewal(date.fromisoformat(row["renewal"]), row["cycle"])
    return nxt, (nxt - today()).days


def monthly_burn() -> float:
    """Total subscription cost normalized to one month: monthly subs count
    in full, yearly subs count at cost/12."""
    total = 0.0
    for r in read_subscriptions():
        cost = float(r["cost"])
        total += cost if r["cycle"] == "monthly" else cost / 12
    return total


def due_soon(days: int = 7) -> list[tuple[dict, date, int]]:
    """Subscriptions renewing within `days` days, sorted by soonest first."""
    result = []
    for r in read_subscriptions():
        nxt, away = subscription_status(r)
        if away <= days:
            result.append((r, nxt, away))
    return sorted(result, key=lambda x: x[2])


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------

def bar_chart(by_cat: dict[str, float], width: int = 30) -> list[str]:
    """Render per-category totals as ASCII bars scaled to the largest one."""
    lines = []
    if not by_cat:
        return lines
    peak = max(by_cat.values())
    name_w = max(len(c) for c in by_cat)
    for cat, total in by_cat.items():
        bars = "█" * round(total / peak * width) if peak else ""
        lines.append(f"  {cat:<{name_w}}  {fmt_money(total):>12}  {bars}")
    return lines


def _print_summary(month: str) -> None:
    total, by_cat = summarize(month)
    print(f"Expenses — {month}")
    print(f"Total: {fmt_money(total)}")
    print()
    for line in bar_chart(by_cat):
        print(line)


def _print_expenses(rows: list[dict]) -> None:
    if not rows:
        print("No expenses recorded.")
        return
    print(f"  {'ID':<4} {'Date':<12} {'Amount':>12}  {'Category':<14} Note")
    for r in rows:
        print(
            f"  {r['id']:<4} {r['date']:<12} {fmt_money(float(r['amount'])):>12}  "
            f"{r['category']:<14} {r['note']}"
        )


def _print_subscriptions(rows: list[dict]) -> None:
    if not rows:
        print("No subscriptions tracked yet.")
        return
    print(f"  {'Name':<20} {'Cost':>10}  {'Cycle':<8} {'Next renewal':<14} {'In'}")
    for r in rows:
        nxt, away = subscription_status(r)
        away_str = "today" if away == 0 else f"{away}d"
        print(
            f"  {r['name']:<20} {fmt_money(float(r['cost'])):>10}  "
            f"{r['cycle']:<8} {nxt.isoformat():<14} {away_str}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="spendwise",
        description="Track expenses and subscriptions from the command line.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="Record an expense")
    a.add_argument("amount", type=float, help="Amount spent (e.g. 250 or 250.50)")
    a.add_argument("category", help="Category (e.g. food, travel)")
    a.add_argument("--note", default="", help="Optional note")
    a.add_argument("--date", default=None, help="Date of expense, YYYY-MM-DD (default: today)")

    l = sub.add_parser("list", help="List recorded expenses")
    l.add_argument("--month", default=None, help="Filter by month, YYYY-MM (default: all)")

    s = sub.add_parser("summary", help="Monthly summary with per-category breakdown")
    s.add_argument("--month", default=None, help="Month, YYYY-MM (default: current month)")

    b = sub.add_parser("budget", help="Per-category monthly budgets")
    bsub = b.add_subparsers(dest="budget_cmd", required=True)
    bs = bsub.add_parser("set", help="Set a monthly budget for a category")
    bs.add_argument("category", help="Category name")
    bs.add_argument("amount", type=float, help="Monthly budget amount")
    bst = bsub.add_parser("status", help="Show budgets vs this month's spending")
    bst.add_argument("--month", default=None, help="Month, YYYY-MM (default: current month)")

    sb = sub.add_parser("sub", help="Manage subscriptions")
    ssub = sb.add_subparsers(dest="sub_cmd", required=True)
    sa = ssub.add_parser("add", help="Add a subscription")
    sa.add_argument("name", help="Subscription name (e.g. Netflix)")
    sa.add_argument("cost", type=float, help="Cost per billing cycle")
    sa.add_argument("cycle", choices=["monthly", "yearly"], help="Billing cycle")
    sa.add_argument("renewal", help="Renewal date, YYYY-MM-DD")
    ssub.add_parser("list", help="List subscriptions with next renewal dates")
    sd = ssub.add_parser("due", help="Show renewals coming up soon")
    sd.add_argument("--days", type=int, default=7, help="Look-ahead window in days (default: 7)")
    sr = ssub.add_parser("remove", help="Remove a subscription")
    sr.add_argument("name", help="Subscription name")

    sub.add_parser("overview", help="This month's spending + subscription burn + upcoming renewals")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "add":
            on = parse_date(args.date) if args.date else None
            row = add_expense(args.amount, args.category, args.note, on)
            month = row["date"][:7]
            print(f"Added: {fmt_money(float(row['amount']))} on {row['category']} ({row['date']})")
            # Immediate feedback if this expense pushed the category over budget.
            for warning in budget_warnings(month):
                if warning.startswith(("OVER BUDGET", "close to budget")) and row["category"] in warning:
                    print(f"  ⚠ {warning}")

        elif args.command == "list":
            rows = read_expenses()
            if args.month:
                month = parse_month(args.month)
                rows = [r for r in rows if r["date"].startswith(month)]
            _print_expenses(rows)

        elif args.command == "summary":
            month = parse_month(args.month) if args.month else today().strftime("%Y-%m")
            _print_summary(month)

        elif args.command == "budget":
            if args.budget_cmd == "set":
                set_budget(args.category, args.amount)
                print(f"Budget set: {args.category.strip().lower()} = {fmt_money(args.amount)}/month")
            else:
                month = parse_month(args.month) if args.month else today().strftime("%Y-%m")
                budgets = read_budgets()
                if not budgets:
                    print("No budgets set yet. Use: spendwise budget set <category> <amount>")
                    return 0
                _, by_cat = summarize(month)
                print(f"Budgets — {month}")
                for category, limit in budgets.items():
                    spent = by_cat.get(category, 0.0)
                    pct = spent / limit * 100 if limit else 0
                    status = "OK"
                    if spent > limit:
                        status = "OVER"
                    elif pct >= 80:
                        status = "close"
                    print(f"  {category:<14} {fmt_money(spent):>12} / {fmt_money(limit):<12} {pct:5.0f}%  {status}")
                print()
                warnings = budget_warnings(month)
                if warnings:
                    for w in warnings:
                        print(f"  ⚠ {w}")
                else:
                    print("  All budgets on track.")

        elif args.command == "sub":
            if args.sub_cmd == "add":
                renewal = parse_date(args.renewal)
                row = add_subscription(args.name, args.cost, args.cycle, renewal)
                nxt, away = subscription_status(row)
                print(f"Added: {row['name']} — {fmt_money(float(row['cost']))}/{row['cycle']}, "
                      f"renews {nxt.isoformat()} ({away}d)")
            elif args.sub_cmd == "list":
                _print_subscriptions(read_subscriptions())
            elif args.sub_cmd == "due":
                if args.days < 0:
                    raise ValueError("--days must be 0 or more")
                upcoming = due_soon(args.days)
                if not upcoming:
                    print(f"No renewals in the next {args.days} day(s).")
                else:
                    print(f"Renewals due in the next {args.days} day(s):")
                    for r, nxt, away in upcoming:
                        away_str = "today" if away == 0 else f"in {away}d"
                        print(f"  {r['name']:<20} {fmt_money(float(r['cost'])):>10}  {nxt.isoformat()} ({away_str})")
            elif args.sub_cmd == "remove":
                if remove_subscription(args.name):
                    print(f"Removed subscription: {args.name}")
                else:
                    print(f"No subscription named {args.name!r}.", file=sys.stderr)
                    return 1

        elif args.command == "overview":
            month = today().strftime("%Y-%m")
            total, by_cat = summarize(month)
            burn = monthly_burn()
            upcoming = due_soon(7)

            print(f"SpendWise overview — {month}")
            print("=" * 40)
            print(f"\nExpenses this month: {fmt_money(total)}")
            if by_cat:
                print("Top categories:")
                for line in bar_chart(dict(list(by_cat.items())[:3])):
                    print(line)
            print(f"\nSubscription burn: {fmt_money(burn)}/month")
            subs = read_subscriptions()
            if subs:
                print(f"  ({len(subs)} active subscription{'s' if len(subs) != 1 else ''})")
            print()
            if upcoming:
                print("Renewals coming up:")
                for r, nxt, away in upcoming:
                    away_str = "today" if away == 0 else f"in {away}d"
                    print(f"  {r['name']:<20} {fmt_money(float(r['cost'])):>10}  {nxt.isoformat()} ({away_str})")
            else:
                print("No renewals in the next 7 days.")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
