"""Budget calculations; these functions read records but never change them.

The report has two views: primary-account cash and category spending.
Internal transfers affect the first, but are excluded from the second.
"""

import calendar
from decimal import Decimal
from collections import defaultdict
from .models import Entry, Category
from django.db.models import Q


def report(plan):
    """Build the year dashboard from the saved plan and transactions."""
    entries = list(
        Entry.objects.filter(date__year=plan.year)
        .select_related("account", "destination")
        .prefetch_related("allocations__category")
    )
    effective_amounts, remaining_by_category = remaining_allowances(entries)
    income_entries = Entry.objects.filter(kind="income").filter(
        Q(income_month__year=plan.year)
        | Q(income_month__isnull=True, date__year=plan.year)
    )
    months = monthly_cash(plan, entries, effective_amounts, income_entries)
    categories = category_spending(plan, entries, remaining_by_category)
    # Category schedules replace (rather than add to) their transaction-based
    # spending forecasts. Primary-account cash remains based on real account flows.
    for index, month in enumerate(months):
        adjustment = sum(
            category["cells"][index]["forecast_adjustment"] for category in categories
        )
        month["spending_net"] -= adjustment
    return months, categories, entries


def remaining_allowances(entries):
    """Subtract categorised purchases from each matching monthly allowance."""
    # Unmatched actual purchases consume the remaining monthly spending allowance.
    # Matched bills retain original_cents and already replace their plan entirely.
    unmatched_spending = defaultdict(int)
    for entry in entries:
        if (
            entry.actual
            and entry.kind in ("expense", "refund")
            and entry.original_cents is None
        ):
            for allocation in entry.allocations.all():
                unmatched_spending[
                    (entry.date.month, entry.account_id, allocation.category_id)
                ] += allocation.amount_cents * (-1 if entry.kind == "refund" else 1)
    effective_amounts = {}
    remaining_by_category = defaultdict(int)
    for entry in entries:
        amount = entry.amount_cents
        if not entry.actual and entry.kind == "expense":
            for allocation in entry.allocations.all():
                key = (entry.date.month, entry.account_id, allocation.category_id)
                # Refunds replenish an allowance, so consumed can be negative.
                consumed = min(allocation.amount_cents, unmatched_spending[key])
                unmatched_spending[key] -= consumed
                amount -= consumed
                remaining_by_category[(entry.date.month, allocation.category_id)] += (
                    allocation.amount_cents - consumed
                )
        effective_amounts[entry.pk] = amount
    return effective_amounts, remaining_by_category


def monthly_cash(plan, entries, effective_amounts, income_entries=None):
    """Carry primary-account cash forward, using actuals in closed months."""
    if income_entries is None:
        income_entries = [entry for entry in entries if entry.kind == "income"]
    balance = plan.opening_cents
    months = []
    for month in range(1, 13):
        active = [
            entry
            for entry in entries
            if entry.date.month == month
            and (entry.actual or month > plan.closed_through)
        ]
        incoming = outgoing = spent = 0
        earned = sum(
            entry.amount_cents
            for entry in income_entries
            if (entry.income_month or entry.date).year == plan.year
            and (entry.income_month or entry.date).month == month
            and (entry.actual or month > plan.closed_through)
        )
        for entry in active:
            amount = effective_amounts[entry.pk]
            if entry.kind == "expense":
                spent += amount
            if entry.kind == "refund":
                spent -= amount
            if entry.account.primary:
                if entry.kind in ("income", "refund", "savings_in"):
                    incoming += amount
                elif entry.kind != "card_payment":
                    outgoing += amount
            if entry.kind == "transfer" and entry.destination.primary:
                incoming += amount
        opening = balance
        balance += incoming - outgoing
        months.append(
            dict(
                number=month,
                name=calendar.month_abbr[month],
                opening=opening / Decimal(100),
                incoming=incoming / Decimal(100),
                outgoing=outgoing / Decimal(100),
                net=(incoming - outgoing) / Decimal(100),
                spending_net=(earned - spent) / Decimal(100),
                closing=balance / Decimal(100),
                closed=month <= plan.closed_through,
            )
        )
    return months


def category_spending(plan, entries, remaining_by_category):
    """Compare the original budget with actual and forecast spending."""
    categories = []
    schedules = {
        schedule.category_id: schedule
        for schedule in plan.category_schedules.select_related("year_plan")
    }
    actual_signs = {
        entry.pk: (-1 if entry.kind == "refund" else 1)
        for entry in entries
        if entry.actual
    }
    for category in Category.objects.all():
        schedule = schedules.get(category.pk)
        budgets = schedule.monthly_amounts(original=True) if schedule else {}
        expected = schedule.monthly_amounts() if schedule else {}
        cells = []
        for month in range(1, 13):
            allocations = [
                allocation
                for entry in entries
                if entry.date.month == month and entry.kind in ("expense", "refund")
                for allocation in entry.allocations.all()
                if allocation.category_id == category.pk
            ]
            original = sum(allocation.original_cents or 0 for allocation in allocations)
            actual = sum(
                allocation.amount_cents * actual_signs[allocation.entry_id]
                for allocation in allocations
                if allocation.entry_id in actual_signs
            )
            forecast = actual + (
                remaining_by_category[(month, category.pk)]
                if month > plan.closed_through
                else 0
            )
            entry_forecast = forecast
            if schedule:
                original = budgets.get(month, 0)
                if month > plan.closed_through:
                    forecast = max(forecast, expected.get(month, 0))
            cells.append(
                dict(
                    budget=original / Decimal(100),
                    actual=actual / Decimal(100),
                    forecast=forecast / Decimal(100),
                    remaining=(original - actual) / Decimal(100),
                    over=actual > original,
                    forecast_adjustment=(forecast - entry_forecast) / Decimal(100),
                )
            )
        categories.append(
            dict(
                id=category.pk,
                name=category.name,
                cells=cells,
                scheduled=bool(schedule),
            )
        )
    return categories
