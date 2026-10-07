from datetime import date
from django.contrib.auth.models import User
from django.test import Client, TestCase
from .models import Account, Category, Entry, YearPlan


class TransactionsPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("transactions-test")
        self.client.force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.bank = Account.objects.create(name="Bank", primary=True)
        self.category = Category.objects.create(name="Food")
        YearPlan.objects.create(year=2026)
        for day in range(1, 26):
            Entry.objects.create(
                date=date(2026, 7, day),
                description=f"Purchase {day}",
                account=self.bank,
                amount_cents=100,
                kind="expense",
                actual=True,
            )
        self.august = Entry.objects.create(
            date=date(2026, 8, 1),
            description="August purchase",
            account=self.bank,
            amount_cents=100,
            kind="expense",
            actual=True,
        )

    def test_month_includes_every_transaction_and_navigation(self):
        response = self.client.get("/transactions/?month=2026-07")
        self.assertEqual(len(response.context["rows"]), 25)
        self.assertNotContains(response, "August purchase")
        self.assertContains(response, "?month=2026-06")
        self.assertContains(response, "?month=2026-08")
        self.assertContains(response, "Save category", count=25)
        self.assertContains(self.client.get("/transactions/"), "August purchase")

    def test_inline_assignment_stays_in_month(self):
        response = self.client.post(
            "/transactions/?month=2026-08",
            {"entry": self.august.pk, "category": self.category.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, f"/transactions/?month=2026-08#transaction-{self.august.pk}"
        )
        self.assertEqual(self.august.allocations.get().category, self.category)
        response = self.client.post(
            "/transactions/?month=2026-08", {"entry": self.august.pk, "category": ""}
        )
        self.assertContains(response, "This field is required.")
        self.assertEqual(self.august.allocations.count(), 1)
        self.assertEqual(
            self.client.post(
                "/transactions/?month=2026-07",
                {"entry": self.august.pk, "category": self.category.pk},
            ).status_code,
            404,
        )

    def test_home_pagination_does_not_change_totals(self):
        first = self.client.get("/?year=2026")
        second = self.client.get("/?year=2026&page=2")
        self.assertEqual(len(first.context["entries"]), 20)
        self.assertEqual(len(second.context["entries"]), 6)
        self.assertEqual(first.context["months"], second.context["months"])
        self.assertEqual(first.context["months"][6]["outgoing"], 25)
        self.assertContains(first, "page=2#money-movements")
        self.assertEqual(self.client.get("/?year=2026&page=bad").status_code, 200)

    def test_empty_invalid_and_year_boundary_months(self):
        self.assertContains(
            self.client.get("/transactions/?month=2027-01"), "No transactions"
        )
        self.assertContains(
            self.client.get("/transactions/?month=2027-01"), "?month=2026-12"
        )
        for month in ("bad", "9999-12", "2026-13"):
            self.assertEqual(
                self.client.get("/transactions/", {"month": month}).status_code, 200
            )

    def test_authentication_and_csrf(self):
        self.assertEqual(Client().get("/transactions/").status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.assertEqual(
            client.post(
                "/transactions/?month=2026-08",
                {"entry": self.august.pk, "category": self.category.pk},
            ).status_code,
            403,
        )
