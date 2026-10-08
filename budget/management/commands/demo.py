from datetime import date, timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from budget.models import (
    Account,
    Category,
    CategorySchedule,
    YearPlan,
    Entry,
    Allocation,
)


class Command(BaseCommand):
    help = "Create fictional sample budget data (only in an empty budget)."

    @transaction.atomic
    def handle(self, *args, **options):
        if Account.objects.exists() or YearPlan.objects.exists():
            raise CommandError("Demo requires an empty budget database.")
        year = date.today().year
        primary = Account.objects.create(name="Everyday account", primary=True)
        card = Account.objects.create(name="Credit card", account_type="credit_card")
        food = Category.objects.create(name="Food")
        fuel = Category.objects.create(name="Fuel")
        registration = Category.objects.create(name="Car registration")
        plan = YearPlan.objects.create(year=year, opening_cents=250000)
        for category, amount, frequency, month, day in [
            (food, 100000, "monthly", 1, 1),
            (fuel, 30000, "monthly", 1, 1),
            (registration, 120000, "annual", 7, 12),
        ]:
            CategorySchedule.objects.create(
                category=category,
                year_plan=plan,
                amount_cents=amount,
                frequency=frequency,
                due_month=month,
                due_day=day,
            )
        for month in range(1, 13):
            Entry.objects.create(
                date=date(year, month, 15),
                description="Monthly salary",
                kind="income",
                account=primary,
                amount_cents=450000,
            )
            transfer = Entry.objects.create(
                date=date(year, month, 1),
                description="Fund card spending",
                kind="transfer",
                account=primary,
                destination=card,
                amount_cents=130000,
            )
            for category, amount in [(food, 100000), (fuel, 30000)]:
                Allocation.objects.create(
                    entry=transfer, category=category, amount_cents=amount
                )
                expense = Entry.objects.create(
                    date=date(year, month, 28),
                    description=f"{category.name} monthly allowance",
                    kind="expense",
                    account=card,
                    amount_cents=amount,
                )
                Allocation.objects.create(
                    entry=expense, category=category, amount_cents=amount
                )
        payday = date(year, 1, 9)
        while payday.year == year:
            Entry.objects.create(
                date=payday,
                description="Fortnightly pay",
                kind="income",
                account=primary,
                amount_cents=180000,
            )
            payday += timedelta(days=14)
        bill = Entry.objects.create(
            date=date(year, 7, 12),
            description="Annual registration",
            kind="expense",
            account=primary,
            amount_cents=120000,
        )
        Allocation.objects.create(
            entry=bill, category=registration, amount_cents=120000
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Fictional demo budget created. Create a login with createsuperuser."
            )
        )
