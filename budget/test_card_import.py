"""Synthetic bank-export examples only; never store real bank records in tests."""

import csv
import io
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .imports import read_rows, save_rows
from .models import Account, Allocation, Category, Entry, YearPlan
from .services import report

HEADERS = [
    "Date",
    "Amount",
    "Account Number",
    "",
    "Transaction Type",
    "Transaction Details",
    "Category",
    "Merchant Name",
    "Processed On",
]


def upload(rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerows(rows)
    return SimpleUploadedFile("fictional.csv", output.getvalue().encode())


def purchase(amount="-10.00", details="FICTIONAL SHOP", processed="30 Sept 26"):
    return [
        "30 Sept 26",
        amount,
        "Card ending 1234",
        "",
        "CREDIT CARD PURCHASE",
        details,
        "Groceries",
        "Example Shop",
        processed,
    ]


class CardImportTests(TestCase):
    def setUp(self):
        self.bank = Account.objects.create(name="Everyday", primary=True)
        self.card = Account.objects.create(name="Card")
        self.food = Category.objects.create(name="Food", account=self.card)
        self.year = YearPlan.objects.create(year=2026, opening_cents=100000)
        self.user = User.objects.create_user("import-test")
        self.client.force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )

    def test_september_blank_column_and_bank_hints(self):
        rows = read_rows(upload([purchase()]))
        self.assertEqual(rows[0]["date"], date(2026, 9, 30))
        self.assertEqual(save_rows(self.card, rows), 1)
        entry = Entry.objects.get()
        self.assertEqual(entry.kind, "expense")
        self.assertEqual(entry.bank_category, "Groceries")
        self.assertEqual(entry.source_label, "Ending 1234")
        self.assertEqual(entry.processed_date, date(2026, 9, 30))
        self.assertEqual(entry.amount_cents, 1000)

    def test_reimport_reordering_and_overlapping_export(self):
        first, second = purchase(), purchase("-20.00", "OTHER FICTIONAL SHOP")
        self.assertEqual(save_rows(self.card, read_rows(upload([first, second]))), 2)
        self.assertEqual(save_rows(self.card, read_rows(upload([second, first]))), 0)
        self.assertEqual(save_rows(self.card, read_rows(upload([second]))), 0)
        second[6] = "Changed bank category"
        self.assertEqual(save_rows(self.card, read_rows(upload([second]))), 0)

    def test_identical_legitimate_purchases_are_not_collapsed(self):
        rows = read_rows(upload([purchase(), purchase()]))
        self.assertEqual(save_rows(self.card, rows), 2)
        self.assertEqual(save_rows(self.card, rows), 0)

    def test_unprocessed_purchase_is_skipped_then_imports_when_processed(self):
        self.assertEqual(
            save_rows(self.card, read_rows(upload([purchase(processed="")]))), 0
        )
        self.assertEqual(save_rows(self.card, read_rows(upload([purchase()]))), 1)

    def test_card_payment_is_not_income_and_can_match_funding_plan(self):
        row = [
            "30 Sept 26",
            "100.00",
            "Card ending 1234",
            "",
            "CREDIT CARD PAYMENT",
            "PAYMENT THANK YOU",
            "Transfers",
            "",
            "30 Sept 26",
        ]
        rows = read_rows(upload([row]))
        save_rows(self.card, rows)
        receipt = Entry.objects.get()
        self.assertEqual(receipt.kind, "card_payment")
        months, _, _ = report(self.year)
        self.assertEqual(months[8]["spending_net"], 0)
        self.assertEqual(months[8]["closing"], 1000)
        plan = Entry.objects.create(
            date=date(2026, 9, 29),
            description="Card funding",
            kind="transfer",
            account=self.bank,
            destination=self.card,
            amount_cents=10000,
        )
        Allocation.objects.create(entry=plan, category=self.food, amount_cents=10000)
        response = self.client.post(f"/entry/{receipt.pk}/match/", {"planned": plan.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Entry.objects.count(), 1)
        months, categories, _ = report(self.year)
        self.assertEqual(months[8]["closing"], 900)
        self.assertEqual(categories[0]["cells"][8]["actual"], 0)
        self.assertEqual(save_rows(self.card, rows), 0)

    def test_bulk_assignment_and_refunds_net_against_spending(self):
        refund = [
            "30 Sept 26",
            "3.00",
            "Card ending 1234",
            "",
            "CREDIT CARD REFUND",
            "FICTIONAL SHOP REFUND",
            "Groceries",
            "Example Shop",
            "30 Sept 26",
        ]
        save_rows(self.card, read_rows(upload([purchase(), refund])))
        response = self.client.post(
            "/assign/",
            {
                "account": self.card.pk,
                "bank_category": "Groceries",
                "category": self.food.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        months, categories, _ = report(self.year)
        self.assertEqual(categories[0]["cells"][8]["actual"], Decimal("7"))
        self.assertEqual(months[8]["closing"], 1000)
        self.assertEqual(months[8]["spending_net"], -7)
        self.assertEqual(Allocation.objects.count(), 2)
        self.client.post(
            "/assign/",
            {
                "account": self.card.pk,
                "bank_category": "Groceries",
                "category": self.food.pk,
            },
        )
        self.assertEqual(Allocation.objects.count(), 2)

    def test_card_export_cannot_target_primary_account(self):
        with self.assertRaises(ValueError):
            save_rows(self.bank, read_rows(upload([purchase()])))
        self.assertEqual(Entry.objects.count(), 0)

    def test_unknown_type_and_wrong_sign_fail_before_any_writes(self):
        for kind, amount in [
            ("UNKNOWN", "-10.00"),
            ("CREDIT CARD PURCHASE", "10.00"),
            ("CREDIT CARD REFUND", "-10.00"),
        ]:
            row = purchase()
            row[4] = kind
            row[1] = amount
            with self.assertRaises(ValueError):
                read_rows(upload([purchase(), row]))
        self.assertEqual(Entry.objects.count(), 0)

    def test_assignment_requires_login_and_csrf(self):
        from django.test import Client

        self.assertEqual(Client().post("/assign/").status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.assertEqual(client.post("/assign/").status_code, 403)

    def test_refund_replenishes_monthly_allowance(self):
        allowance = Entry.objects.create(
            date=date(2026, 9, 1),
            description="Monthly food",
            kind="expense",
            account=self.card,
            amount_cents=10000,
        )
        Allocation.objects.create(
            entry=allowance, category=self.food, amount_cents=10000
        )
        refund = Entry.objects.create(
            date=date(2026, 9, 10),
            description="Refund",
            kind="refund",
            account=self.card,
            amount_cents=1000,
            actual=True,
        )
        Allocation.objects.create(entry=refund, category=self.food, amount_cents=1000)
        _, categories, _ = report(self.year)
        cell = categories[0]["cells"][8]
        self.assertEqual(cell["actual"], -10)
        self.assertEqual(cell["remaining"], 110)
        self.assertEqual(cell["forecast"], 100)
