"""Category schedules do not own accounts or rewrite transactions."""

from datetime import date
from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase, Client
from .forms import CategoryForm
from .models import Account, Category, CategorySchedule, YearPlan, Entry, Allocation
from .services import report


class CategoryScheduleTests(TestCase):
    def setUp(self):
        self.year = YearPlan.objects.create(year=2026, opening_cents=100000)
        self.category = Category.objects.create(name="Food")
        self.bank = Account.objects.create(name="Primary", primary=True)
        self.card = Account.objects.create(name="Card")
        self.user = User.objects.create_user("category-test")
        self.client.force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )

    def schedule(self, frequency="monthly", month=1, day=1):
        return CategorySchedule.objects.create(
            category=self.category,
            year_plan=self.year,
            amount_cents=10000,
            frequency=frequency,
            due_month=month,
            due_day=day,
        )

    def test_category_has_no_account_field(self):
        self.assertNotIn("account", {field.name for field in Category._meta.fields})
        self.assertNotIn("account", CategoryForm().fields)

    def test_all_frequencies_and_dates(self):
        for frequency, month, expected in [
            ("monthly", 7, list(range(1, 13))),
            ("quarterly", 2, [2, 5, 8, 11]),
            ("six_monthly", 3, [3, 9]),
            ("annual", 7, [7]),
        ]:
            schedule = CategorySchedule(
                category=self.category,
                year_plan=self.year,
                amount_cents=10000,
                frequency=frequency,
                due_month=month,
                due_day=15,
            )
            self.assertEqual(
                schedule.due_dates(), [date(2026, m, 15) for m in expected]
            )

    def test_short_months_and_leap_year(self):
        schedule = self.schedule(day=31)
        self.assertEqual(schedule.due_dates()[1], date(2026, 2, 28))
        leap = YearPlan.objects.create(year=2028)
        schedule.year_plan = leap
        self.assertEqual(schedule.due_dates()[1], date(2028, 2, 29))
        self.assertEqual(schedule.due_dates()[2], date(2028, 3, 31))

    def test_annual_amount_only_in_due_month_without_inventing_cash_flow(self):
        self.schedule("annual", 7, 12)
        months, categories, _ = report(self.year)
        cells = categories[0]["cells"]
        self.assertEqual(cells[5]["budget"], 0)
        self.assertEqual(cells[6]["budget"], 100)
        self.assertEqual(cells[6]["forecast"], 100)
        self.assertEqual(months[6]["spending_net"], -100)
        self.assertEqual(months[6]["closing"], 1000)

    def test_actuals_from_multiple_accounts_and_refund(self):
        self.schedule()
        for account, kind, amount in [
            (self.bank, "expense", 4000),
            (self.card, "expense", 7000),
            (self.card, "refund", 500),
        ]:
            entry = Entry.objects.create(
                date=date(2026, 7, 5),
                description="Example",
                kind=kind,
                account=account,
                amount_cents=amount,
                actual=True,
            )
            Allocation.objects.create(
                entry=entry, category=self.category, amount_cents=amount
            )
        months, categories, _ = report(self.year)
        cell = categories[0]["cells"][6]
        self.assertEqual(cell["actual"], Decimal("105"))
        self.assertEqual(cell["remaining"], -5)
        self.assertEqual(cell["forecast"], 105)
        self.assertEqual(months[6]["closing"], 960)
        self.assertEqual(months[6]["spending_net"], -105)

    def test_planned_payment_does_not_double_scheduled_budget(self):
        self.schedule("annual", 7, 1)
        entry = Entry.objects.create(
            date=date(2026, 7, 1),
            description="Bill",
            kind="expense",
            account=self.bank,
            amount_cents=10000,
        )
        Allocation.objects.create(
            entry=entry, category=self.category, amount_cents=10000
        )
        months, categories, _ = report(self.year)
        self.assertEqual(categories[0]["cells"][6]["budget"], 100)
        self.assertEqual(months[6]["spending_net"], -100)
        self.assertEqual(months[6]["closing"], 900)

    def test_edit_preserves_original_schedule_and_other_year(self):
        schedule = self.schedule("annual", 7, 12)
        next_year = YearPlan.objects.create(year=2027)
        CategorySchedule.objects.create(
            category=self.category,
            year_plan=next_year,
            amount_cents=5000,
            frequency="monthly",
            due_month=1,
            due_day=1,
        )
        response = self.client.post(
            f"/category/{self.category.pk}/edit/?year=2026",
            {
                "name": "Food",
                "year_plan": self.year.pk,
                "amount": "125.00",
                "frequency": "annual",
                "due_month": 8,
                "due_day": 14,
            },
        )
        self.assertEqual(response.status_code, 302)
        schedule.refresh_from_db()
        self.assertEqual(schedule.original_amount_cents, 10000)
        self.assertEqual(schedule.monthly_amounts(original=True), {7: 10000})
        self.assertEqual(schedule.monthly_amounts(), {8: 12500})
        self.assertEqual(
            CategorySchedule.objects.get(year_plan=next_year).amount_cents, 5000
        )

    def test_closed_month_uses_actual_not_future_allowance(self):
        self.schedule()
        self.year.closed_through = 7
        months, categories, _ = report(self.year)
        self.assertEqual(categories[0]["cells"][6]["budget"], 100)
        self.assertEqual(categories[0]["cells"][6]["forecast"], 0)
        self.assertEqual(months[6]["spending_net"], 0)

    def test_create_and_edit_pages(self):
        response = self.client.post(
            "/add/category/",
            {
                "name": "Registration",
                "year_plan": self.year.pk,
                "amount": "1200.00",
                "frequency": "annual",
                "due_month": 7,
                "due_day": 12,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get("/categories/?year=2026"), "Registration")
        category = Category.objects.get(name="Registration")
        response = self.client.get(f"/category/{category.pk}/edit/?year=2026")
        self.assertEqual(response.context["form"].initial["amount"], 1200)
        self.assertEqual(response.context["form"].initial["due_day"], 12)

    def test_invalid_schedule_form(self):
        base = {
            "name": "Invalid",
            "year_plan": self.year.pk,
            "amount": "10.00",
            "frequency": "monthly",
            "due_month": 1,
            "due_day": 1,
        }
        for field, value in [
            ("amount", "-1"),
            ("due_month", 13),
            ("due_day", 0),
            ("due_day", 32),
            ("frequency", "weekly"),
        ]:
            with self.subTest(field=field, value=value):
                form = CategoryForm({**base, field: value})
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_authentication_and_csrf(self):
        self.assertEqual(Client().get("/categories/").status_code, 302)
        self.assertEqual(
            Client().post(f"/category/{self.category.pk}/edit/").status_code, 302
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.assertEqual(
            client.post(f"/category/{self.category.pk}/edit/").status_code, 403
        )
