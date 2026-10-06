from django.db import models
from django.core.exceptions import ValidationError

class Account(models.Model):
    name = models.CharField(max_length=100, unique=True)
    primary = models.BooleanField(default=False)
    def clean(self):
        if self.primary and Account.objects.filter(primary=True).exclude(pk=self.pk).exists():
            raise ValidationError("Only one primary transaction account is allowed.")
    def __str__(self): return self.name

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    def __str__(self): return self.name

class YearPlan(models.Model):
    year = models.PositiveIntegerField(unique=True)
    opening_cents = models.BigIntegerField(default=0, help_text="Primary account balance on 1 January, in cents")
    closed_through = models.PositiveSmallIntegerField(default=0, help_text="Last completed month: 0–12. Completed months use actuals only.")
    def clean(self):
        if not 2000 <= self.year <= 2100 or not 0 <= self.closed_through <= 12:
            raise ValidationError("Use a year from 2000–2100 and a closed month from 0–12.")
    def __str__(self): return str(self.year)

class Entry(models.Model):
    KINDS = [("income", "Income"), ("expense", "Expense"), ("transfer", "Internal transfer")]
    date = models.DateField()
    description = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=KINDS)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="entries", help_text="Receiving account for income; paying account otherwise")
    destination = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="incoming")
    amount_cents = models.PositiveBigIntegerField()
    actual = models.BooleanField(default=False)
    original_cents = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    import_key = models.CharField(max_length=64, null=True, blank=True, unique=True)
    class Meta: ordering = ["date", "pk"]
    def clean(self):
        if self.kind == "transfer" and (not self.destination_id or self.destination_id == self.account_id):
            raise ValidationError("Transfers need a different destination account.")
        if self.kind != "transfer" and self.destination_id:
            raise ValidationError("Only transfers have a destination.")
        if not self.amount_cents: raise ValidationError("Amount must be positive.")
    def save(self, *args, **kwargs):
        if self.pk is None and not self.actual: self.original_cents = self.amount_cents
        super().save(*args, **kwargs)
    def __str__(self): return self.description

class Allocation(models.Model):
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="allocations")
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    amount_cents = models.PositiveBigIntegerField()
    original_cents = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    def save(self, *args, **kwargs):
        if self.pk is None and not self.entry.actual: self.original_cents = self.amount_cents
        super().save(*args, **kwargs)
