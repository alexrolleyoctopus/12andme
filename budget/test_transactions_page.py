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
        self.assertContains(response, ">Save all</button>", count=1)
        self.assertContains(self.client.get("/transactions/"), "August purchase")

    def test_save_all_stays_in_month_and_preserves_blanks(self):
        key = f"entry_{self.august.pk}-category"
        response = self.client.post(
            "/transactions/?month=2026-08", {key: self.category.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/transactions/?month=2026-08")
        self.assertEqual(self.august.allocations.get().category, self.category)
        self.client.post("/transactions/?month=2026-08", {key: ""})
        self.assertEqual(self.august.allocations.count(), 1)
        self.client.post("/transactions/?month=2026-07", {key: self.category.pk})
        self.assertEqual(self.august.allocations.count(), 1)

    def test_bulk_validation_is_atomic(self):
        entries = list(Entry.objects.filter(date__month=7)[:2])
        data = {
            f"entry_{entries[0].pk}-category": self.category.pk,
            f"entry_{entries[1].pk}-category": "999999",
        }
        response = self.client.post("/transactions/?month=2026-07", data)
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(entries[0].allocations.exists())
        data[f"entry_{entries[1].pk}-category"] = self.category.pk
        self.client.post("/transactions/?month=2026-07", data)
        for entry in entries:
            self.assertEqual(entry.allocations.get().category, self.category)

    def test_calendar_month_picker(self):
        response = self.client.get("/transactions/?year=2026&month=7")
        self.assertEqual(response.context["month"], date(2026, 7, 1))
        self.assertContains(response, 'class="month-grid"')
        self.assertContains(response, '<button name="month"', count=12)
        self.assertNotContains(response, 'type="month"')

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
            self.client.get("/transactions/?month=2027-01"), "No transactions match"
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

    def test_uncategorised_filter_ignores_zero_allocations(self):
        from .models import Allocation

        entries = list(Entry.objects.filter(date__month=7)[:2])
        Allocation.objects.create(
            entry=entries[0], category=self.category, amount_cents=100
        )
        Allocation.objects.create(
            entry=entries[1], category=self.category, amount_cents=0, original_cents=100
        )
        response = self.client.get("/transactions/?month=2026-07&uncategorised=1")
        ids = [row["entry"].pk for row in response.context["rows"]]
        self.assertNotIn(entries[0].pk, ids)
        self.assertIn(entries[1].pk, ids)
        self.assertContains(response, "&amp;uncategorised=1")
        response = self.client.post(
            "/transactions/?month=2026-07&uncategorised=1",
            {f"entry_{entries[1].pk}-category": self.category.pk},
        )
        self.assertEqual(response.url, "/transactions/?month=2026-07&uncategorised=1")

    def test_income_and_savings_change_earnings_not_cash(self):
        from .services import report

        self.august.kind = "income"
        self.august.income_month = date(2026, 9, 1)
        self.august.save()
        key = f"entry_{self.august.pk}-receipt_type"
        response = self.client.post("/transactions/?month=2026-08", {key: "savings_in"})
        self.assertEqual(response.status_code, 302)
        self.august.refresh_from_db()
        self.assertEqual(self.august.kind, "savings_in")
        self.assertIsNone(self.august.income_month)
        months, _, _ = report(YearPlan.objects.get(year=2026))
        self.assertEqual(months[7]["incoming"], 1)
        self.assertEqual(months[7]["spending_net"], 0)
        self.client.post("/transactions/?month=2026-08", {key: "income"})
        months, _, _ = report(YearPlan.objects.get(year=2026))
        self.assertEqual(months[7]["incoming"], 1)
        self.assertEqual(months[7]["spending_net"], 1)

    def test_receipt_selector_cannot_reverse_expense(self):
        self.client.post(
            "/transactions/?month=2026-08",
            {f"entry_{self.august.pk}-receipt_type": "savings_in"},
        )
        self.august.refresh_from_db()
        self.assertEqual(self.august.kind, "expense")
