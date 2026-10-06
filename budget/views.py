import csv, io, hashlib
from datetime import date
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from .models import Account, Category, YearPlan, Entry, Allocation
from .forms import AccountForm, CategoryForm, YearForm, EntryForm
from .services import report

@login_required
def dashboard(request):
    plans = YearPlan.objects.order_by("year")
    try: year = int(request.GET.get("year", date.today().year))
    except ValueError: year = date.today().year
    plan = plans.filter(year=year).first()
    months, categories, entries = report(plan) if plan else ([], [], [])
    return render(request, "budget/dashboard.html", dict(plan=plan, plans=plans, months=months, categories=categories, entries=entries, year=year, needs_review=sum(1 for e in entries if e.import_key and e.original_cents is None and (e.kind == "income" or not e.allocations.exists()))))

@login_required
def add(request, kind):
    forms = {"account": AccountForm, "category": CategoryForm, "year": YearForm, "entry": EntryForm}
    if kind not in forms: return redirect("dashboard")
    form = forms[kind](request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic(): form.save()
        messages.success(request, "Saved successfully.")
        return redirect("dashboard")
    return render(request, "budget/form.html", {"form": form, "title": "Add " + kind})

@login_required
@require_POST
def settle(request, pk):
    entry = get_object_or_404(Entry, pk=pk)
    entry.actual = True
    entry.save()
    messages.success(request, "Marked as actual. Its original budget is preserved.")
    return redirect("dashboard")

@login_required
def import_csv(request):
    if request.method == "POST":
        try:
            upload = request.FILES["file"]
            if upload.size > 2 * 1024 * 1024: raise ValueError("Maximum CSV size is 2 MB.")
            account = Account.objects.get(pk=request.POST.get("account"))
            reader = csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig")))
            if not {"id", "date", "description", "amount"}.issubset(reader.fieldnames or []):
                raise ValueError("CSV needs id,date,description,amount columns.")
            rows = list(reader)
            with transaction.atomic():
                added = 0
                for row in rows:
                    amount = Decimal(row["amount"])
                    if not row["id"] or not row["id"].strip() or not amount.is_finite() or amount == 0 or abs(amount) > Decimal("9999999999.99") or amount != amount.quantize(Decimal("0.01")):
                        raise ValueError("Each row needs a stable ID and a nonzero amount with at most two decimal places.")
                    key = hashlib.sha256(f"{account.pk}:{row['id']}".encode()).hexdigest()
                    _, created = Entry.objects.get_or_create(import_key=key, defaults={"date": date.fromisoformat(row["date"]), "description": (row["description"] or "Imported transaction")[:200], "kind": "income" if amount > 0 else "expense", "account": account, "amount_cents": int(abs(amount)*100), "actual": True})
                    added += created
            messages.success(request, f"Imported {added} entries; skipped {len(rows)-added} existing IDs. Review transfers and match planned entries in admin before relying on forecasts.")
            return redirect("dashboard")
        except (ValueError, ArithmeticError, UnicodeError, KeyError, TypeError, Account.DoesNotExist, csv.Error) as exc:
            messages.error(request, str(exc))
    return render(request, "budget/import.html", {"accounts": Account.objects.all()})

@login_required
def edit_entry(request, pk):
    entry = get_object_or_404(Entry, pk=pk)
    initial = {"amount": Decimal(entry.amount_cents)/100, "splits": "\n".join(f"{a.category.name}: {Decimal(a.amount_cents)/100:.2f}" for a in entry.allocations.select_related("category"))}
    form = EntryForm(request.POST or None, instance=entry, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic(): form.save()
        messages.success(request, "Movement updated.")
        return redirect("dashboard")
    return render(request, "budget/form.html", {"form": form, "title": "Edit movement"})

@login_required
def match_entry(request, pk):
    imported = get_object_or_404(Entry, pk=pk, actual=True, import_key__isnull=False)
    candidates = Entry.objects.filter(actual=False, account=imported.account, date__year=imported.date.year)
    if request.method == "POST":
        with transaction.atomic():
            planned = get_object_or_404(candidates, pk=request.POST.get("planned"))
            allocations = list(planned.allocations.all())
            if allocations and sum(a.amount_cents for a in allocations) != imported.amount_cents:
                messages.error(request, "First edit the planned amount and its category splits to match the imported total, then match again. The original budget is preserved.")
            else:
                key = imported.import_key
                imported.delete()
                planned.actual = True
                planned.amount_cents = imported.amount_cents
                planned.date = imported.date
                planned.import_key = key
                planned.save()
                messages.success(request, "Matched: the actual replaces the planned movement without counting it twice.")
                return redirect("dashboard")
    return render(request, "budget/match.html", {"entry": imported, "candidates": candidates})
