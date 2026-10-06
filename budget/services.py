import calendar
from collections import defaultdict
from .models import Entry, Category

def report(plan):
    entries = list(Entry.objects.filter(date__year=plan.year).select_related("account", "destination").prefetch_related("allocations__category"))
    # Unmatched actual purchases consume the remaining monthly spending allowance.
    # Matched bills retain original_cents and already replace their plan entirely.
    pools = defaultdict(int)
    for e in entries:
        if e.actual and e.kind == "expense" and e.original_cents is None:
            for a in e.allocations.all():
                pools[(e.date.month, e.account_id, a.category_id)] += a.amount_cents
    effective = {}
    category_remaining = defaultdict(int)
    for e in entries:
        amount = e.amount_cents
        if not e.actual and e.kind == "expense":
            for a in e.allocations.all():
                key = (e.date.month, e.account_id, a.category_id)
                consumed = min(a.amount_cents, pools[key])
                pools[key] -= consumed
                amount -= consumed
                category_remaining[(e.date.month, a.category_id)] += a.amount_cents - consumed
        effective[e.pk] = amount
    balance = plan.opening_cents
    months = []
    for month in range(1, 13):
        active = [e for e in entries if e.date.month == month and (e.actual or month > plan.closed_through)]
        incoming = outgoing = earned = spent = 0
        for e in active:
            amount = effective[e.pk]
            if e.kind == "income": earned += amount
            if e.kind == "expense": spent += amount
            if e.account.primary:
                if e.kind == "income": incoming += amount
                else: outgoing += amount
            if e.kind == "transfer" and e.destination.primary: incoming += amount
        opening = balance
        balance += incoming - outgoing
        months.append(dict(number=month, name=calendar.month_abbr[month], opening=opening/100, incoming=incoming/100, outgoing=outgoing/100, net=(incoming-outgoing)/100, spending_net=(earned-spent)/100, closing=balance/100, closed=month <= plan.closed_through))
    categories = []
    actual_ids = {e.pk for e in entries if e.actual}
    for category in Category.objects.all():
        cells = []
        for month in range(1, 13):
            allocations = [a for e in entries if e.date.month == month and e.kind == "expense" for a in e.allocations.all() if a.category_id == category.pk]
            original = sum(a.original_cents or 0 for a in allocations)
            actual = sum(a.amount_cents for a in allocations if a.entry_id in actual_ids)
            forecast = actual + (category_remaining[(month, category.pk)] if month > plan.closed_through else 0)
            cells.append(dict(budget=original/100, actual=actual/100, forecast=forecast/100, remaining=(original-actual)/100, over=actual > original))
        categories.append(dict(name=category.name, cells=cells))
    return months, categories, entries
