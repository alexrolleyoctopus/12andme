"""User-facing forms: validate dollars here and save whole cents in the database."""

import calendar
from datetime import timedelta
from decimal import Decimal
from django import forms
from .models import Account, Category, YearPlan, Entry, Allocation


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name", "primary"]


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "account"]


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
        fields = ["date", "description", "kind", "account", "destination", "actual"]
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
            clone = Entry.objects.create(
                date=current,
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
