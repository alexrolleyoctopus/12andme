"""Page handlers: validate forms, call budget logic, then render or redirect."""

from datetime import date
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_http_methods
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from .models import YearPlan, Entry, Allocation, Category
from .forms import (
    AccountForm,
    CategoryForm,
    YearForm,
    EntryForm,
    ImportForm,
    AssignmentForm,
)
from .imports import read_rows, save_rows
from .services import report


@login_required
def dashboard(request):
    plans = YearPlan.objects.order_by("year")
    try:
        year = int(request.GET.get("year", date.today().year))
    except ValueError:
        year = date.today().year
    if not 2000 <= year <= 2100:
        year = date.today().year
    plan = plans.filter(year=year).first()
    months, categories, entries = report(plan) if plan else ([], [], [])
    return render(
        request,
        "budget/dashboard.html",
        dict(
            plan=plan,
            plans=plans,
            months=months,
            categories=categories,
            entries=entries,
            year=year,
            needs_review=sum(
                1
                for entry in entries
                if entry.import_key
                and entry.original_cents is None
                and (
                    entry.kind in ("income", "card_payment")
                    or not entry.allocations.exists()
                )
            ),
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def add(request, kind):
    forms = {
        "account": AccountForm,
        "category": CategoryForm,
        "year": YearForm,
        "entry": EntryForm,
    }
    if kind not in forms:
        return redirect("dashboard")
    initial = {}
    if kind == "category":
        selected_year = request.GET.get("year", str(date.today().year))
        if selected_year.isdigit() and len(selected_year) == 4:
            initial["year_plan"] = YearPlan.objects.filter(
                year=int(selected_year)
            ).first()
    form = forms[kind](
        request.POST if request.method == "POST" else None, initial=initial
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
        messages.success(request, "Saved successfully.")
        if kind == "category":
            return redirect(f"/categories/?year={form.cleaned_data['year_plan'].year}")
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
@require_http_methods(["GET", "POST"])
def import_csv(request):
    form = ImportForm(
        request.POST if request.method == "POST" else None, request.FILES or None
    )
    if request.method == "POST" and form.is_valid():
        try:
            rows = read_rows(form.cleaned_data["file"])
            added = save_rows(form.cleaned_data["account"], rows)
        except ValueError as error:
            form.add_error("file", str(error))
        else:
            pending = sum(row["pending"] for row in rows)
            duplicates = len(rows) - added - pending
            messages.success(
                request,
                f"Imported {added} settled entries; skipped {duplicates} duplicates and {pending} unprocessed rows. Assign categories and review card payments before relying on the forecast.",
            )
            return redirect("dashboard")
    return render(request, "budget/import.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def edit_entry(request, pk):
    entry = get_object_or_404(Entry, pk=pk)
    initial = {
        "amount": Decimal(entry.amount_cents) / 100,
        "splits": "\n".join(
            f"{a.category.name}: {Decimal(a.amount_cents)/100:.2f}"
            for a in entry.allocations.select_related("category").filter(
                amount_cents__gt=0
            )
        ),
    }
    form = EntryForm(
        request.POST if request.method == "POST" else None,
        instance=entry,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
        messages.success(request, "Movement updated.")
        return redirect("dashboard")
    return render(request, "budget/form.html", {"form": form, "title": "Edit movement"})


@login_required
@require_http_methods(["GET", "POST"])
def match_entry(request, pk):
    imported = get_object_or_404(Entry, pk=pk, actual=True, import_key__isnull=False)
    # An incoming bank row cannot replace an outgoing payment (or vice versa).
    if imported.kind == "card_payment":
        # Receipt on a card matches the outgoing funding plan from another account.
        candidates = Entry.objects.filter(
            actual=False,
            kind="transfer",
            destination=imported.account,
            date__year=imported.date.year,
        )
    else:
        allowed_kinds = (
            ["income"]
            if imported.kind == "income"
            else ["refund"] if imported.kind == "refund" else ["expense", "transfer"]
        )
        candidates = Entry.objects.filter(
            actual=False,
            account=imported.account,
            date__year=imported.date.year,
            kind__in=allowed_kinds,
        )
    if imported.original_cents is not None:
        messages.error(request, "This transaction has already been matched to a plan.")
        return redirect("dashboard")
    if request.method == "POST":
        with transaction.atomic():
            try:
                planned_id = int(request.POST.get("planned", ""))
            except (TypeError, ValueError):
                planned_id = 0
            planned = get_object_or_404(candidates, pk=planned_id)
            allocations = list(planned.allocations.all())
            if (
                allocations
                and sum(a.amount_cents for a in allocations) != imported.amount_cents
            ):
                messages.error(
                    request,
                    "First edit the planned amount and its category splits to match the imported total, then match again. The original budget is preserved.",
                )
            else:
                key = imported.import_key
                # Preserve bank hints when the actual replaces its planned movement.
                for field in (
                    "bank_category",
                    "bank_merchant",
                    "bank_type",
                    "source_label",
                    "processed_date",
                ):
                    setattr(planned, field, getattr(imported, field))
                imported.delete()
                planned.actual = True
                planned.amount_cents = imported.amount_cents
                planned.date = imported.date
                planned.import_key = key
                planned.save()
                messages.success(
                    request,
                    "Matched: the actual replaces the planned movement without counting it twice.",
                )
                return redirect("dashboard")
    return render(
        request, "budget/match.html", {"entry": imported, "candidates": candidates}
    )


@login_required
@require_http_methods(["GET", "POST"])
def assign_categories(request):
    """Assign one bank category to a budget category without overwriting prior work."""
    form = AssignmentForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            entries = Entry.objects.filter(
                actual=True,
                kind__in=["expense", "refund"],
                bank_category=form.cleaned_data["bank_category"],
                account=form.cleaned_data["account"],
                allocations__isnull=True,
            )
            count = 0
            for entry in entries:
                Allocation.objects.create(
                    entry=entry,
                    category=form.cleaned_data["category"],
                    amount_cents=entry.amount_cents,
                )
                count += 1
        messages.success(
            request,
            f"Assigned {count} previously uncategorised purchases/refunds. Existing assignments were preserved.",
        )
        return redirect("dashboard")
    return render(
        request, "budget/form.html", {"form": form, "title": "Assign bank categories"}
    )


@login_required
def categories(request):
    plans = YearPlan.objects.order_by("year")
    try:
        year = int(request.GET.get("year", date.today().year))
        if not 2000 <= year <= 2100:
            year = date.today().year
    except ValueError:
        year = date.today().year
    plan = plans.filter(year=year).first()
    schedules = (
        {
            schedule.category_id: schedule
            for schedule in plan.category_schedules.select_related("year_plan")
        }
        if plan
        else {}
    )
    rows = [
        {"category": category, "schedule": schedules.get(category.pk)}
        for category in Category.objects.order_by("name")
    ]
    return render(
        request,
        "budget/categories.html",
        {"rows": rows, "plans": plans, "plan": plan, "year": year},
    )


@login_required
@require_http_methods(["GET", "POST"])
def edit_category(request, pk):
    category = get_object_or_404(Category, pk=pk)
    try:
        year = int(request.GET.get("year", date.today().year))
        if not 2000 <= year <= 2100:
            year = date.today().year
    except ValueError:
        year = date.today().year
    plan = YearPlan.objects.filter(year=year).first()
    form = CategoryForm(
        request.POST if request.method == "POST" else None,
        instance=category,
        initial={"year_plan": plan},
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
        messages.success(
            request,
            "Category and yearly schedule saved. Existing transactions are unchanged.",
        )
        return redirect(f"/categories/?year={form.cleaned_data['year_plan'].year}")
    return render(request, "budget/form.html", {"form": form, "title": "Edit category"})
