from datetime import date
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from .models import Account, Category, YearPlan, Entry, Allocation
from .services import report
from .forms import EntryForm


class BudgetTests(TestCase):
    def setUp(self):
        self.bank = Account.objects.create(name="Bank", primary=True)
        self.card = Account.objects.create(name="Card")
        self.food = Category.objects.create(name="Food", account=self.card)
        self.plan = YearPlan.objects.create(year=2026, opening_cents=200000)
        self.user = User.objects.create_user("alex", password="test-secret-123")

    def entry(self, **kw):
        return Entry.objects.create(
            date=date(2026, 7, 1),
            description="Test",
            account=self.bank,
            amount_cents=120000,
            kind="expense",
            **kw,
        )

    def test_transfer_reduces_cash_but_not_category_spend(self):
        e = self.entry(destination=self.card, actual=True)
        e.kind = "transfer"
        e.save()
        Allocation.objects.create(entry=e, category=self.food, amount_cents=120000)
        purchase = Entry.objects.create(
            date=e.date,
            description="Groceries",
            account=self.card,
            kind="expense",
            amount_cents=40000,
            actual=True,
        )
        Allocation.objects.create(
            entry=purchase, category=self.food, amount_cents=40000
        )
        months, cats, _ = report(self.plan)
        self.assertEqual(months[6]["closing"], 800)
        self.assertEqual(months[7]["opening"], 800)
        self.assertEqual(cats[0]["cells"][6]["actual"], 400)

    def test_annual_bill_only_affects_due_month_and_rolls_forward(self):
        self.entry()
        months, _, _ = report(self.plan)
        self.assertEqual(months[5]["closing"], 2000)
        self.assertEqual(months[6]["closing"], 800)
        self.assertEqual(months[11]["closing"], 800)

    def test_closed_months_ignore_unrealised_plans(self):
        self.entry()
        self.plan.closed_through = 7
        self.assertEqual(report(self.plan)[0][6]["closing"], 2000)

    def test_original_budget_survives_adjustment(self):
        e = self.entry()
        Allocation.objects.create(entry=e, category=self.food, amount_cents=120000)
        data = {
            "date": "2026-07-01",
            "description": "Bill",
            "account": self.bank.pk,
            "kind": "expense",
            "amount": "1250.00",
            "splits": "Food: 1250.00",
            "actual": True,
        }
        form = EntryForm(data, instance=e)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        cell = report(self.plan)[1][0]["cells"][6]
        self.assertEqual(cell["budget"], 1200)
        self.assertEqual(cell["actual"], 1250)

    def test_login_required(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/?year=2026"), "Your year, in balance.")

    def test_csv_atomic_and_idempotent(self):
        self.client.force_login(self.user)
        csv = b"id,date,description,amount\nx,2026-07-01,Food,-10.00\n"
        for _ in range(2):
            self.client.post(
                "/import/",
                {"account": self.bank.pk, "file": SimpleUploadedFile("x.csv", csv)},
            )
        self.assertEqual(Entry.objects.count(), 1)
        bad = b"id,date,description,amount\ny,2026-07-01,Food,-10.00\nz,bad,Oops,4\n"
        self.client.post(
            "/import/",
            {"account": self.bank.pk, "file": SimpleUploadedFile("x.csv", bad)},
        )
        self.assertEqual(Entry.objects.count(), 1)

    def test_match_replaces_plan(self):
        self.client.force_login(self.user)
        planned = self.entry()
        actual = self.entry(actual=True, import_key="unique")
        self.client.post(f"/entry/{actual.pk}/match/", {"planned": planned.pk})
        self.assertEqual(Entry.objects.count(), 1)
        planned.refresh_from_db()
        self.assertTrue(planned.actual)
        self.assertEqual(planned.original_cents, 120000)

    def test_bucket_actuals_consume_allowance_without_double_count(self):
        e = self.entry()
        Allocation.objects.create(entry=e, category=self.food, amount_cents=120000)
        for amount in [20000, 30000]:
            a = Entry.objects.create(
                date=e.date,
                description="Food",
                account=self.bank,
                kind="expense",
                amount_cents=amount,
                actual=True,
            )
            Allocation.objects.create(entry=a, category=self.food, amount_cents=amount)
        months, categories, _ = report(self.plan)
        self.assertEqual(months[6]["closing"], 800)
        cell = categories[0]["cells"][6]
        self.assertEqual(cell["actual"], 500)
        self.assertEqual(cell["forecast"], 1200)

    def test_fortnightly_schedule_uses_real_paydays(self):
        form = EntryForm(
            {
                "date": "2026-01-09",
                "description": "Pay",
                "account": self.bank.pk,
                "kind": "income",
                "amount": "100.00",
                "repeat": "fortnightly",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(Entry.objects.count(), 26)
        self.assertEqual(Entry.objects.filter(date__month=1).count(), 2)
        self.assertEqual(Entry.objects.filter(date__month=5).count(), 3)
