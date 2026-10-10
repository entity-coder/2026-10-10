# SpendWise

A command-line money tracker with two halves: **expenses** (add spending, monthly summaries, per-category budgets) and **subscriptions** (track recurring payments, see your monthly burn, get warned about upcoming renewals). One tool, one daily habit — checking your money.

Built for the daily-portfolio series (2026-10-10, python-script slot).

## Features

- **Expense tracking** — `add <amount> <category> [--note] [--date]`; categories normalize to lowercase so `Food` and `food` don't split into two buckets.
- **Monthly summaries** — per-category totals with an ASCII bar chart scaled to the largest category.
- **Budgets** — `budget set <category> <amount>` sets a monthly limit; `budget status` compares this month's spending against it. You get an `OVER BUDGET` warning, plus a "close" heads-up at 80%+. Adding an expense that pushes a category over budget warns you immediately.
- **Subscriptions** — `sub add <name> <cost> <monthly|yearly> <renewal YYYY-MM-DD>`; `sub list` shows each sub's next renewal and days away; `sub due [--days N]` (default 7) lists renewals coming up.
- **Overview** — one screen: this month's spending, top categories, total subscription burn (yearly subs counted at cost ÷ 12), and renewals due in the next 7 days.
- **Renewal math that survives real life** — if the stored renewal date is in the past, it rolls forward by whole billing cycles (monthly clamps to the end of short months, e.g. Jan 31 → Feb 28; yearly handles Feb 29 on non-leap years).

## How to run

Python 3, no dependencies — pure standard library.

```bash
python3 spendwise.py add 250 food --note "lunch"
python3 spendwise.py summary
python3 spendwise.py budget set food 5000
python3 spendwise.py budget status
python3 spendwise.py sub add Netflix 199 monthly 2026-11-02
python3 spendwise.py sub due
python3 spendwise.py overview
```

Run the smoke tests with:

```bash
python3 test_spendwise.py
```

## Example session

```text
$ python3 spendwise.py add 250 food --note "lunch"
Added: ₹250.00 on food (2026-10-10)

$ python3 spendwise.py add 1200 travel --note "bus tickets"
Added: ₹1,200.00 on travel (2026-10-10)

$ python3 spendwise.py budget set food 5000
Budget set: food = ₹5,000.00/month

$ python3 spendwise.py summary
Expenses — 2026-10
Total: ₹1,949.00

  travel     ₹1,200.00  ██████████████████████████████
  food         ₹749.00  ███████████████████

$ python3 spendwise.py sub add Netflix 199 monthly 2026-11-02
Added: Netflix — ₹199.00/monthly, renews 2026-11-02 (23d)

$ python3 spendwise.py sub add "iCloud+" 75 monthly 2026-10-15
Added: iCloud+ — ₹75.00/monthly, renews 2026-10-15 (5d)

$ python3 spendwise.py overview
SpendWise overview — 2026-10
========================================

Expenses this month: ₹1,949.00
Top categories:
  travel     ₹1,200.00  ██████████████████████████████
  food         ₹749.00  ███████████████████

Subscription burn: ₹274.00/month
  (2 active subscriptions)

Renewals coming up:
  iCloud+                  ₹75.00  2026-10-15 (in 5d)
```

## Where your data lives

`~/.spendwise/` (or wherever `$SPENDWISE_DIR` points — the tests use a temp dir):

- `expenses.csv` — one row per expense: id, date, amount, category, note
- `subscriptions.csv` — name, cost, cycle, renewal
- `budgets.json` — `{"food": 5000}` — per-category monthly limits

Everything is plain text, so you can open the files in any editor or spreadsheet. Created automatically on first run.

## Why no AI here?

Deliberately. A money tool needs to be deterministic and fully offline — you don't want a language model deciding what you spent or when something renews. Trust matters more than cleverness in finance tooling, so SpendWise does exact arithmetic and nothing else.

## Interview notes

**What is this?** A single-file CLI (`spendwise.py`, ~450 lines) that tracks day-to-day expenses and recurring subscriptions, with monthly reports, budgets, and renewal reminders. Plus a `test_spendwise.py` suite (14 unittest cases).

**Key technical decisions and why:**

- *Pure stdlib, zero dependencies* — `argparse`, `csv`, `json`, `datetime` cover everything the tool needs. For a script this size, pulling in Click/pandas would add install friction with no real payoff; stdlib keeps `python3 spendwise.py` runnable anywhere.
- *CSV + JSON files instead of SQLite* — the data model is two flat tables and one key-value map, all tiny. CSVs are human-readable and open in any spreadsheet, which is genuinely useful for expense data. The honest trade-off: no transactions, no indexes, no concurrent-write safety — fine for a single-user CLI, wrong for anything multi-user.
- *Categories normalized to lowercase* — avoids the classic "Food" vs "food" split that silently corrupts per-category totals and budget comparisons. Display case is sacrificed for data consistency.
- *Renewal dates roll forward instead of erroring* — if you enter last month's renewal date by mistake, `next_renewal()` advances it by whole billing cycles until it lands on today or later. A finance tool that rejects slightly-stale input trains users to stop entering data; this one does the obviously-correct thing.
- *Immediate over-budget warning on `add`, not just in `budget status`* — feedback at the moment of the action is what actually changes behavior.
- *`SPENDWISE_DIR` env var* — the one concession to testability: tests point it at a temp dir so the suite never touches real data. No config file or flags needed.

**Main trade-offs / limitations:**

- Single-user, single-machine: no sync, no multi-device. The data dir path is the whole "deployment story".
- Amounts use `float`, not `Decimal` — rounding error is negligible at personal-finance scale, but a banking system would need `Decimal`.
- No recurring-expense support (only subscriptions recur); no export command (though the CSVs *are* the export).
- Budgets are per calendar month with no rollover of unspent amounts.
- The ASCII bar chart scales to the largest category, so small categories can render as empty bars — visually fine for a terminal, but a proportional scale would show more detail.

**How I'd extend it:**

- A `trend` command showing month-over-month totals as a sparkline, to spot lifestyle creep.
- Configurable warning thresholds (currently hardcoded at 80% and 100%).
- `Decimal` for amounts if this ever grew beyond personal use, plus a locked-down file permission on the data dir.
- Recurring expenses (rent, EMIs) as a third data type alongside subscriptions.
