"""User-facing forms: validate dollars here and save whole cents in the database."""

import calendar
from datetime import timedelta
from decimal import Decimal
from django import forms
from .models import Account, Category, YearPlan, Entry, Allocation, CategorySchedule


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name", "primary"]


class CategoryForm(forms.ModelForm):
    """Edit a category and its selected year's schedule together."""

    year_plan = forms.ModelChoiceField(
        queryset=YearPlan.objects.order_by("year"), label="Budget year"
    )
    amount = forms.DecimalField(
        decimal_places=2,
        max_digits=12,
        min_value=Decimal("0.01"),
        label="Amount per occurrence ($)",
    )
    frequency = forms.ChoiceField(
        choices=CategorySchedule.FREQUENCIES, label="Recurrence"
    )
    due_month = forms.TypedChoiceField(
        choices=CategorySchedule.MONTHS,
        coerce=int,
        initial=1,
        label="First due month",
        help_text="Ignored for monthly schedules. Other schedules start in this month and repeat through December.",
    )
    due_day = forms.IntegerField(
        min_value=1,
        max_value=31,
        initial=1,
        label="Day of month",
        help_text="If this day does not exist in a month, use that month's last day.",
    )

    class Meta:
        model = Category
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        year_plan = self.initial.get("year_plan")
        if self.instance.pk and year_plan:
            schedule = CategorySchedule.objects.filter(
                category=self.instance, year_plan=year_plan
            ).first()
            if schedule:
                self.initial.update(
                    amount=Decimal(schedule.amount_cents) / 100,
                    frequency=schedule.frequency,
                    due_month=schedule.due_month,
                    due_day=schedule.due_day,
                )

    def save(self, commit=True):
        if not commit:
            raise ValueError(
                "CategoryForm saves its annual schedule together; commit=False is not supported."
            )
        category = super().save(commit)
        schedule = CategorySchedule.objects.filter(
            category=category, year_plan=self.cleaned_data["year_plan"]
        ).first()
        if schedule is None:
            schedule = CategorySchedule(
                category=category, year_plan=self.cleaned_data["year_plan"]
            )
        schedule.amount_cents = int(self.cleaned_data["amount"] * 100)
        schedule.frequency = self.cleaned_data["frequency"]
        schedule.due_month = (
            self.cleaned_data["due_month"] if schedule.frequency != "monthly" else 1
        )
        schedule.due_day = self.cleaned_data["due_day"]
        schedule.save()
        return category


class YearForm(forms.ModelForm):
    opening = forms.DecimalField(
        decimal_places=2, max_digits=12, label="Opening primary-account balance ($)"
    )

    class Meta:
        model = YearPlan
        fields = ["year", "closed_through"]

    def save(self, commit=True):
        self.instance.opening_cents = int(self.cleaned_data["opening"] * 100)
        return super().save(commit)


class EntryForm(forms.ModelForm):
    income_month = forms.DateField(
        required=False,
        input_formats=["%Y-%m"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
        label="Income budget month",
        help_text="For income only. Leave blank to use the payment month. Cash still arrives on the transaction date. Repeated income keeps the same month offset.",
    )

    repeat = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Once"),
            ("monthly", "Monthly through December"),
            ("quarterly", "Quarterly through December"),
            ("fortnightly", "Every 14 days through December"),
        ],
        help_text="Applies to new planned entries only. Each occurrence can be edited separately.",
    )
    amount = forms.DecimalField(
        decimal_places=2, max_digits=12, min_value=Decimal("0.01"), label="Amount ($)"
    )
    splits = forms.CharField(
        required=False,
        max_length=10000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="One category per line, e.g. Food: 1000.00. Splits must total the amount. Leave blank for uncategorised entries.",
    )

    class Meta:
        model = Entry
        fields = [
            "date",
            "description",
            "kind",
            "income_month",
            "account",
            "destination",
            "actual",
        ]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        data = super().clean()
        self.instance.amount_cents = int(data.get("amount", 0) * 100)
        self.parsed_splits = []
        for line in data.get("splits", "").splitlines():
            try:
                name, value = line.rsplit(":", 1)
                category = Category.objects.get(name__iexact=name.strip())
                amount = Decimal(value.strip())
                if (
                    not amount.is_finite()
                    or amount <= 0
                    or amount > Decimal("9999999999.99")
                    or amount != amount.quantize(Decimal("0.01"))
                ):
                    raise ValueError()
                self.parsed_splits.append((category, int(amount * 100)))
            except (
                ValueError,
                ArithmeticError,
                Category.DoesNotExist,
                Category.MultipleObjectsReturned,
            ):
                self.add_error(
                    "splits",
                    "Use an existing category and positive two-decimal amount on every line.",
                )
        if len({c.pk for c, _ in self.parsed_splits}) != len(self.parsed_splits):
            self.add_error("splits", "Use each category only once.")
        if (
            self.parsed_splits
            and sum(v for _, v in self.parsed_splits) != self.instance.amount_cents
        ):
            self.add_error("splits", "Category amounts must total the entry amount.")
        if data.get("repeat") and (self.instance.pk or data.get("actual")):
            self.add_error(
                "repeat", "Repeat is only available for new planned entries."
            )
        return data

    def save(self, commit=True):
        if not commit:
            raise ValueError(
                "EntryForm saves its splits together; commit=False is not supported."
            )
        was_existing = bool(self.instance.pk)
        entry = super().save(commit)
        existing = {split.category_id: split for split in entry.allocations.all()}
        seen = set()
        for category, amount in self.parsed_splits:
            if category.pk in existing:
                allocation = existing[category.pk]
                allocation.amount_cents = amount
                allocation.save()
            else:
                allocation = Allocation.objects.create(
                    entry=entry, category=category, amount_cents=amount
                )
                if was_existing:
                    allocation.original_cents = 0
                    allocation.save()
            seen.add(category.pk)
        # Keep the original budget even when a category is removed from the forecast.
        for old in entry.allocations.exclude(category_id__in=seen):
            if old.original_cents:
                old.amount_cents = 0
                old.save()
            else:
                old.delete()
        repeat = self.cleaned_data.get("repeat")
        if repeat and not was_existing and not entry.actual:
            self.save_repeated_entries(entry, repeat)
        return entry

    def save_repeated_entries(self, entry, repeat):
        """Copy this planned entry to later due dates in the same calendar year."""
        current = entry.date
        while True:
            if repeat == "fortnightly":
                current += timedelta(days=14)
            else:
                month = current.month + (1 if repeat == "monthly" else 3)
                if month > 12:
                    break
                current = current.replace(
                    month=month,
                    day=min(
                        entry.date.day, calendar.monthrange(current.year, month)[1]
                    ),
                )
            if current.year != entry.date.year:
                break
            income_month = None
            if entry.income_month:
                offset = (
                    (entry.income_month.year - entry.date.year) * 12
                    + entry.income_month.month
                    - entry.date.month
                )
                target = current.year * 12 + current.month - 1 + offset
                income_month = current.replace(
                    year=target // 12, month=target % 12 + 1, day=1
                )
            clone = Entry.objects.create(
                date=current,
                income_month=income_month,
                description=entry.description,
                kind=entry.kind,
                account=entry.account,
                destination=entry.destination,
                amount_cents=entry.amount_cents,
            )
            for category, amount in self.parsed_splits:
                Allocation.objects.create(
                    entry=clone, category=category, amount_cents=amount
                )


class ImportForm(forms.Form):
    set_opening = forms.BooleanField(
        required=False,
        label="Set 1 January starting cash from this export",
        help_text="Primary account only. Replaces the export year's opening balance. Accepts 1 January through your latest transaction, with consistent running balances and a transaction dated 1 January. Export all transactions from 1 January; leave unchecked for files starting later.",
    )

    account = forms.ModelChoiceField(queryset=Account.objects.all())
    file = forms.FileField(label="CSV file (up to 2 MB)")


class AssignmentForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=Account.objects.all(), label="Imported account"
    )
    bank_category = forms.ChoiceField(label="Bank category")
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        label="Your budget category",
        help_text="Apply to all unassigned purchases and refunds with this bank category in the selected account. You can edit or split individual entries afterwards.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        names = (
            Entry.objects.filter(
                actual=True, kind__in=["expense", "refund"], allocations__isnull=True
            )
            .exclude(bank_category="")
            .order_by("bank_category")
            .values_list("bank_category", flat=True)
            .distinct()
        )
        self.fields["bank_category"].choices = [(name, name) for name in names]


class TransactionCategoryForm(forms.Form):
    category = forms.ModelChoiceField(
        queryset=Category.objects.order_by("name"),
        empty_label="Choose a category",
        help_text="Assign the full transaction amount to this category. This replaces any current split.",
    )

    def save(self, entry):
        category = self.cleaned_data["category"]
        allocation, created = Allocation.objects.get_or_create(
            entry=entry,
            category=category,
            defaults={"amount_cents": entry.amount_cents},
        )
        if not created:
            allocation.amount_cents = entry.amount_cents
            allocation.save()
        # Reclassification must not rewrite the original planned budget.
        for other in entry.allocations.exclude(pk=allocation.pk):
            if other.original_cents:
                other.amount_cents = 0
                other.save()
            else:
                other.delete()
        if created and not entry.actual:
            allocation.original_cents = 0
            allocation.save()


class TransactionReviewForm(TransactionCategoryForm):
    """Classify incoming money without reversing an outgoing transaction."""

    receipt_type = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Keep current type"),
            ("income", "Income"),
            ("savings_in", "Transfer from savings"),
        ],
        label="Incoming money",
    )

    def __init__(self, *args, entry, **kwargs):
        super().__init__(*args, **kwargs)
        if entry.kind not in ("income", "savings_in"):
            del self.fields["receipt_type"]

    def save(self, entry):
        if self.cleaned_data.get("category"):
            super().save(entry)
        kind = self.cleaned_data.get("receipt_type")
        if kind:
            entry.kind = kind
            if kind != "income":
                entry.income_month = None
            entry.save()
