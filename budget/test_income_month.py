"""Income allocation changes the spending budget, never the bank balance."""

from datetime import date
from django.test import TestCase
from django.contrib.auth.models import User
from .models import Account, Entry, YearPlan
from .forms import EntryForm
from .services import report


class IncomeMonthTests(TestCase):
    def setUp(self):
        self.bank = Account.objects.create(name="Bank", primary=True)
        self.plan = YearPlan.objects.create(year=2026, opening_cents=0)

    def income(self, paid=date(2026, 7, 31), month=date(2026, 8, 1), actual=True):
        return Entry.objects.create(
            date=paid,
            income_month=month,
            kind="income",
            description="Salary",
            account=self.bank,
            amount_cents=100000,
            actual=actual,
        )

    def test_cash_and_budget_use_separate_months(self):
        self.income()
        months, _, _ = report(self.plan)
        self.assertEqual(months[6]["incoming"], 1000)
        self.assertEqual(months[6]["closing"], 1000)
        self.assertEqual(months[6]["spending_net"], 0)
        self.assertEqual(months[7]["incoming"], 0)
        self.assertEqual(months[7]["spending_net"], 1000)

    def test_blank_retains_existing_behavior(self):
        self.income(month=None)
        months, _, _ = report(self.plan)
        self.assertEqual(months[6]["spending_net"], 1000)

    def test_cross_year_does_not_move_cash_or_count_income_twice(self):
        self.income(paid=date(2026, 12, 31), month=date(2027, 1, 1))
        next_plan = YearPlan.objects.create(year=2027, opening_cents=100000)
        current, _, _ = report(self.plan)
        following, _, _ = report(next_plan)
        self.assertEqual(current[11]["incoming"], 1000)
        self.assertEqual(current[11]["spending_net"], 0)
        self.assertEqual(following[0]["incoming"], 0)
        self.assertEqual(following[0]["closing"], 1000)
        self.assertEqual(following[0]["spending_net"], 1000)

    def test_closed_budget_month_excludes_unreceived_plan(self):
        self.income(actual=False)
        self.plan.closed_through = 8
        months, _, _ = report(self.plan)
        self.assertEqual(months[7]["spending_net"], 0)

    def form(self, **changes):
        data = dict(
            date="2026-11-30",
            income_month="2026-12",
            kind="income",
            description="Salary",
            account=self.bank.pk,
            amount="1000",
            repeat="monthly",
        )
        data.update(changes)
        return EntryForm(data)

    def test_month_picker_and_repeats_keep_offset(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        entries = list(Entry.objects.order_by("date"))
        self.assertEqual(entries[0].income_month, date(2026, 12, 1))
        self.assertEqual(entries[1].income_month, date(2027, 1, 1))

    def test_invalid_month_or_non_income_rejected(self):
        for changes in (
            {"income_month": "2026-13"},
            {"kind": "expense"},
            {"income_month": "1999-12"},
        ):
            self.assertFalse(self.form(**changes).is_valid())

    def test_matching_preserves_plans_month(self):
        planned = self.income(actual=False)
        imported = self.income(paid=date(2026, 7, 30), month=None)
        imported.import_key = "salary-import"
        imported.save()
        user = User.objects.create_user("income-test")
        self.client.force_login(
            user, backend="django.contrib.auth.backends.ModelBackend"
        )
        response = self.client.post(
            f"/entry/{imported.pk}/match/", {"planned": planned.pk}
        )
        self.assertEqual(response.status_code, 302)
        planned.refresh_from_db()
        self.assertEqual(planned.income_month, date(2026, 8, 1))
        self.assertEqual(planned.date, date(2026, 7, 30))
        self.assertEqual(Entry.objects.count(), 1)
