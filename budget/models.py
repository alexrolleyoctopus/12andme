"""Database records for one shared household.

Entry means one planned or actual money movement. Allocation is its category split.
Money is stored as integer cents so calculations never use floating-point amounts.
"""

from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator


class Account(models.Model):
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
        if (
            self.primary
            and Account.objects.filter(primary=True).exclude(pk=self.pk).exists()
        ):
            raise ValidationError("Only one primary transaction account is allowed.")

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)

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


class Entry(models.Model):
    KINDS = [
        ("income", "Income"),
        ("expense", "Expense"),
        ("refund", "Refund"),
        ("card_payment", "Card payment — source needs review"),
        ("transfer", "Internal transfer"),
    ]
    date = models.DateField()
    description = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=KINDS)
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="entries",
        help_text="Receiving account for income; paying account otherwise",
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
