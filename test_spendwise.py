#!/usr/bin/env python3
"""Smoke tests for SpendWise.

Run with:  python3 test_spendwise.py

Uses a throwaway SPENDWISE_DIR so your real data is never touched.
"""

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

# Point SpendWise at a temp dir BEFORE importing (data_dir() reads the env var).
TMP = tempfile.TemporaryDirectory()
os.environ["SPENDWISE_DIR"] = TMP.name

import spendwise  # noqa: E402


class SpendWiseTestBase(unittest.TestCase):
    def setUp(self):
        # Fresh, empty store for every test so tests never see each other's rows.
        for f in Path(TMP.name).iterdir():
            if f.is_file():
                f.unlink()


class ExpensesTest(SpendWiseTestBase):
    def test_add_and_summary(self):
        spendwise.add_expense(250, "food", "lunch")
        spendwise.add_expense(100, "Food")  # category normalizes to lowercase
        spendwise.add_expense(50, "travel")
        month = date.today().strftime("%Y-%m")
        total, by_cat = spendwise.summarize(month)
        self.assertEqual(total, 400.0)
        self.assertEqual(by_cat["food"], 350.0)
        self.assertEqual(by_cat["travel"], 50.0)

    def test_add_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            spendwise.add_expense(0, "food")
        with self.assertRaises(ValueError):
            spendwise.add_expense(-10, "food")
        with self.assertRaises(ValueError):
            spendwise.add_expense(10, "   ")

    def test_summary_empty_month(self):
        total, by_cat = spendwise.summarize("1999-01")
        self.assertEqual(total, 0.0)
        self.assertEqual(by_cat, {})


class BudgetsTest(unittest.TestCase):
    def test_over_budget_warning(self):
        spendwise.set_budget("food", 300)
        spendwise.add_expense(400, "food")
        warnings = spendwise.budget_warnings(date.today().strftime("%Y-%m"))
        self.assertTrue(any(w.startswith("OVER BUDGET") and "food" in w for w in warnings))

    def test_close_to_budget_warning(self):
        spendwise.set_budget("travel", 100)
        spendwise.add_expense(85, "travel")  # 85% -> "close" warning
        warnings = spendwise.budget_warnings(date.today().strftime("%Y-%m"))
        self.assertTrue(any("close to budget" in w and "travel" in w for w in warnings))

    def test_no_warning_when_on_track(self):
        spendwise.set_budget("books", 500)
        spendwise.add_expense(100, "books")
        warnings = spendwise.budget_warnings(date.today().strftime("%Y-%m"))
        self.assertFalse([w for w in warnings if "books" in w])

    def test_budget_rejects_non_positive(self):
        with self.assertRaises(ValueError):
            spendwise.set_budget("food", 0)


class SubscriptionsTest(unittest.TestCase):
    def test_add_list_remove(self):
        renewal = (date.today() + timedelta(days=5)).isoformat()
        spendwise.add_subscription("Netflix", 199, "monthly", date.fromisoformat(renewal))
        rows = spendwise.read_subscriptions()
        self.assertEqual(len(rows), 1)
        self.assertTrue(spendwise.remove_subscription("netflix"))  # case-insensitive
        self.assertEqual(spendwise.read_subscriptions(), [])
        self.assertFalse(spendwise.remove_subscription("netflix"))  # already gone

    def test_duplicate_name_rejected(self):
        renewal = (date.today() + timedelta(days=30)).isoformat()
        spendwise.add_subscription("Spotify", 119, "monthly", date.fromisoformat(renewal))
        with self.assertRaises(ValueError):
            spendwise.add_subscription("spotify", 119, "monthly", date.fromisoformat(renewal))
        spendwise.remove_subscription("Spotify")

    def test_past_renewal_rolls_forward(self):
        # A renewal date 40 days in the past, monthly: should roll forward
        # to the next date that hasn't passed.
        past = date.today() - timedelta(days=40)
        nxt = spendwise.next_renewal(past, "monthly")
        self.assertGreaterEqual(nxt, date.today())
        self.assertLess((nxt - date.today()).days, 31)

    def test_yearly_renewal_math(self):
        past = date.today() - timedelta(days=400)
        nxt = spendwise.next_renewal(past, "yearly")
        self.assertGreaterEqual(nxt, date.today())
        self.assertLess((nxt - date.today()).days, 366)

    def test_month_end_clamping(self):
        # Jan 31 + 1 month must not crash on February.
        self.assertEqual(spendwise.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))

    def test_due_soon(self):
        near = (date.today() + timedelta(days=3)).isoformat()
        far = (date.today() + timedelta(days=30)).isoformat()
        spendwise.add_subscription("Near", 50, "monthly", date.fromisoformat(near))
        spendwise.add_subscription("Far", 50, "monthly", date.fromisoformat(far))
        upcoming = spendwise.due_soon(7)
        names = [r["name"] for r, _, _ in upcoming]
        self.assertIn("Near", names)
        self.assertNotIn("Far", names)
        spendwise.remove_subscription("Near")
        spendwise.remove_subscription("Far")

    def test_monthly_burn(self):
        t = date.today() + timedelta(days=60)
        spendwise.add_subscription("M", 120, "monthly", t)
        spendwise.add_subscription("Y", 1200, "yearly", t)
        self.assertAlmostEqual(spendwise.monthly_burn(), 220.0)  # 120 + 1200/12
        spendwise.remove_subscription("M")
        spendwise.remove_subscription("Y")


if __name__ == "__main__":
    unittest.main(verbosity=1)
