"""Fictional examples of the headerless transaction-account CSV."""

from datetime import date
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from .imports import read_rows, save_rows
from .models import Account, Entry


def parse(text):
    return read_rows(SimpleUploadedFile("bank.csv", text.encode("utf-8-sig")))


class BankImportTests(TestCase):
    def setUp(self):
        self.bank = Account.objects.create(name="Primary", primary=True)

    def test_dates_signs_description_and_primary_account(self):
        rows = parse(
            '08/10/2026,-12.00,"Shop, city",+100.00\n07/10/2026,+50.00,Pay,+112.00\n'
        )
        self.assertEqual(rows[0]["date"], date(2026, 10, 8))
        self.assertEqual(rows[0]["description"], "Shop, city")
        self.assertEqual([r["kind"] for r in rows], ["expense", "income"])
        self.assertEqual(save_rows(self.bank, rows), 2)
        self.assertEqual(Entry.objects.get(kind="expense").amount_cents, 1200)
        self.assertEqual(save_rows(self.bank, rows), 0)

    def test_balance_distinguishes_identical_purchases_and_overlap(self):
        first = "08/10/2026,-10.00,Shop,+90.00\n"
        second = "08/10/2026,-10.00,Shop,+80.00\n"
        self.assertEqual(save_rows(self.bank, parse(first + second)), 2)
        self.assertEqual(save_rows(self.bank, parse(second)), 0)
        self.assertEqual(save_rows(self.bank, parse(second + first)), 0)

    def test_exact_repeated_rows_preserved_and_reimported_safely(self):
        row = "08/10/2026,-10.00,Shop,-10.00\n"
        self.assertEqual(save_rows(self.bank, parse(row * 2)), 2)
        self.assertEqual(save_rows(self.bank, parse(row * 2)), 0)

    def test_rejects_bad_rows_before_saving(self):
        for row in (
            "31/02/2026,-10,Shop,100",
            "08/10/2026,NaN,Shop,100",
            "08/10/2026,-10,Shop,Infinity",
            "08/10/2026,-10.001,Shop,100",
            "08/10/2026,0,Shop,100",
            "08/10/2026,-10,Shop",
            "08/10/2026,-10,Shop,100,extra",
        ):
            with self.subTest(row=row), self.assertRaises(ValueError):
                parse(row + "\n")
        self.assertFalse(Entry.objects.exists())

    def test_optional_opening_cash_both_orders_and_reimport(self):
        from .models import YearPlan

        plan = YearPlan.objects.create(year=2026, opening_cents=777)
        first = "01/01/2026,-20.00,Purchase,80.00\n"
        last = "31/12/2026,50.00,Pay,130.00\n"
        rows = parse(last + first)
        save_rows(self.bank, rows)
        plan.refresh_from_db()
        self.assertEqual(plan.opening_cents, 777)
        self.assertEqual(save_rows(self.bank, rows, set_opening=True), 0)
        plan.refresh_from_db()
        self.assertEqual(plan.opening_cents, 10000)
        save_rows(self.bank, parse(first + last), set_opening=True)
        plan.refresh_from_db()
        self.assertEqual(plan.opening_cents, 10000)

    def test_invalid_opening_import_changes_nothing(self):
        from .models import YearPlan

        plan = YearPlan.objects.create(year=2026, opening_cents=777)
        for text in (
            "01/07/2026,-20,Purchase,80\n31/12/2026,50,Pay,130\n",
            "01/01/2026,-20,Purchase,80\n31/12/2026,50,Pay,999\n",
            "01/01/2025,-20,Purchase,80\n31/12/2026,50,Pay,130\n",
        ):
            with self.assertRaises(ValueError):
                save_rows(self.bank, parse(text), set_opening=True)
        plan.refresh_from_db()
        self.assertEqual(plan.opening_cents, 777)
        self.assertFalse(Entry.objects.exists())

    def test_opening_requires_primary_balance_export_and_existing_year(self):
        rows = parse("01/01/2026,-20,Purchase,80\n31/12/2026,50,Pay,130\n")
        other = Account.objects.create(name="Other")
        with self.assertRaises(ValueError):
            save_rows(other, rows, set_opening=True)
        with self.assertRaises(ValueError):
            save_rows(self.bank, rows, set_opening=True)
        with self.assertRaises(ValueError):
            save_rows(
                self.bank,
                parse("id,date,description,amount\na,2026-01-01,Pay,100\n"),
                set_opening=True,
            )
        self.assertFalse(Entry.objects.exists())

    def test_year_to_date_sets_baseline_in_either_order(self):
        from .models import YearPlan

        plan = YearPlan.objects.create(year=2026, opening_cents=999)
        first = "01/01/2026,-20,Purchase,80\n"
        latest = "08/10/2026,50,Pay,130\n"
        for text in (first + latest, latest + first):
            save_rows(self.bank, parse(text), set_opening=True)
            plan.refresh_from_db()
            self.assertEqual(plan.opening_cents, 10000)
        self.assertEqual(Entry.objects.count(), 2)

    def test_january_first_only_can_establish_baseline(self):
        from .models import YearPlan

        plan = YearPlan.objects.create(year=2026)
        save_rows(self.bank, parse("01/01/2026,-20,Purchase,80\n"), set_opening=True)
        plan.refresh_from_db()
        self.assertEqual(plan.opening_cents, 10000)
