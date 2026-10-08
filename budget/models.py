"""Database records for one shared household.

Entry means one planned or actual money movement. Allocation is its category split.
Money is stored as integer cents so calculations never use floating-point amounts.
"""

import calendar
from datetime import date
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator


class Account(models.Model):
    account_type = models.CharField(
        max_length=12,
        default="transaction",
        choices=[
            ("transaction", "Transaction"),
            ("savings", "Savings"),
            ("credit_card", "Credit Card"),
        ],
    )

    name = models.CharField(max_length=100, unique=True)
    primary = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["primary"],
                condition=models.Q(primary=True),
                name="one_primary_account",
            )
        ]

    def clean(self):
        if self.primary and self.account_type == "credit_card":
            raise ValidationError("The primary cash account cannot be a credit card.")
        if (
            self.primary
            and Account.objects.filter(primary=True).exclude(pk=self.pk).exists()
        ):
            raise ValidationError("Only one primary transaction account is allowed.")

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class YearPlan(models.Model):
    year = models.PositiveIntegerField(
        unique=True, validators=[MinValueValidator(2000), MaxValueValidator(2100)]
    )
    opening_cents = models.BigIntegerField(
        default=0, help_text="Primary account balance on 1 January, in cents"
    )
    closed_through = models.PositiveSmallIntegerField(
        default=0,
        validators=[MaxValueValidator(12)],
        help_text="Last completed month: 0–12. Completed months use actuals only.",
    )

    def __str__(self):
        return str(self.year)


class CategorySchedule(models.Model):
    """One category's spending plan for one year, independent of bank accounts."""

    FREQUENCIES = [
        ("monthly", "Monthly"),
        ("quarterly", "Quarterly"),
        ("six_monthly", "Every six months"),
        ("annual", "Annual"),
    ]
    MONTHS = [(month, calendar.month_name[month]) for month in range(1, 13)]
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="schedules"
    )
    year_plan = models.ForeignKey(
        YearPlan, on_delete=models.CASCADE, related_name="category_schedules"
    )
    amount_cents = models.PositiveBigIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(999999999999)]
    )
    frequency = models.CharField(max_length=12, choices=FREQUENCIES)
    due_month = models.PositiveSmallIntegerField(default=1, choices=MONTHS)
    due_day = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(31)]
    )
    # Keep the initial schedule for planned-versus-actual comparisons after edits.
    original_amount_cents = models.PositiveBigIntegerField(editable=False)
    original_frequency = models.CharField(max_length=12, editable=False)
    original_due_month = models.PositiveSmallIntegerField(editable=False)
    original_due_day = models.PositiveSmallIntegerField(editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "year_plan"], name="one_category_schedule_per_year"
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk is None:
            for field in ("amount_cents", "frequency", "due_month", "due_day"):
                setattr(self, "original_" + field, getattr(self, field))
        super().save(*args, **kwargs)

    def due_dates(self, original=False):
        """Generate due dates in this year; day 31 clamps to shorter month ends."""
        prefix = "original_" if original else ""
        frequency = getattr(self, prefix + "frequency")
        day = getattr(self, prefix + "due_day")
        first_month = (
            1 if frequency == "monthly" else getattr(self, prefix + "due_month")
        )
        interval = {"monthly": 1, "quarterly": 3, "six_monthly": 6, "annual": 12}[
            frequency
        ]
        year = self.year_plan.year
        return [
            date(year, month, min(day, calendar.monthrange(year, month)[1]))
            for month in range(first_month, 13, interval)
        ]

    def monthly_amounts(self, original=False):
        amount = self.original_amount_cents if original else self.amount_cents
        return {due.month: amount for due in self.due_dates(original)}

    def __str__(self):
        return f"{self.category} · {self.year_plan}"


class Entry(models.Model):
    KINDS = [
        ("income", "Income"),
        ("savings_in", "Transfer from savings"),
        ("expense", "Expense"),
        ("refund", "Refund"),
        ("card_payment", "Card payment — source needs review"),
        ("transfer", "Internal transfer"),
    ]
    matched_transfer = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="card_receipt",
        editable=False,
    )
    date = models.DateField()
    income_month = models.DateField(
        null=True,
        blank=True,
        help_text="Income budget month (use its first day). Blank uses the payment month.",
    )
    description = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=KINDS)
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="entries",
        help_text="Receiving account for income or savings transfers; paying account otherwise",
    )
    destination = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="incoming",
    )
    amount_cents = models.PositiveBigIntegerField()
    actual = models.BooleanField(default=False)
    original_cents = models.PositiveBigIntegerField(
        null=True, blank=True, editable=False
    )
    import_key = models.CharField(max_length=64, null=True, blank=True, unique=True)

    # Bank hints are preserved for review; they are not budget category assignments.
    bank_category = models.CharField(max_length=200, blank=True)
    bank_merchant = models.CharField(max_length=200, blank=True)
    bank_type = models.CharField(max_length=80, blank=True)
    source_label = models.CharField(max_length=80, blank=True)
    processed_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["date", "pk"]

    def clean(self):
        if self.income_month:
            if self.kind != "income":
                raise ValidationError(
                    {"income_month": "Only income can have a budget month."}
                )
            if self.income_month.day != 1 or not 2000 <= self.income_month.year <= 2100:
                raise ValidationError(
                    {"income_month": "Choose a month between 2000 and 2100."}
                )
        if self.date and not 2000 <= self.date.year <= 2100:
            raise ValidationError({"date": "Choose a date between 2000 and 2100."})
        if self.kind == "transfer" and (
            not self.destination_id or self.destination_id == self.account_id
        ):
            raise ValidationError("Transfers need a different destination account.")
        if self.kind != "transfer" and self.destination_id:
            raise ValidationError("Only transfers have a destination.")
        if not self.amount_cents:
            raise ValidationError("Amount must be positive.")

    def save(self, *args, **kwargs):
        if self.pk is None and not self.actual:
            self.original_cents = self.amount_cents
        super().save(*args, **kwargs)

    def __str__(self):
        return self.description


class Allocation(models.Model):
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "category"], name="one_split_per_category"
            )
        ]

    entry = models.ForeignKey(
        Entry, on_delete=models.CASCADE, related_name="allocations"
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    amount_cents = models.PositiveBigIntegerField()
    original_cents = models.PositiveBigIntegerField(
        null=True, blank=True, editable=False
    )

    def save(self, *args, **kwargs):
        if self.pk is None and not self.entry.actual:
            self.original_cents = self.amount_cents
        super().save(*args, **kwargs)
