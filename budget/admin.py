from django.contrib import admin
from django.forms.models import BaseInlineFormSet
from django.core.exceptions import ValidationError
from .models import Account, Category, YearPlan, Entry, Allocation, CategorySchedule


class SplitFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        rows = [
            f.cleaned_data
            for f in self.forms
            if f.cleaned_data and not f.cleaned_data.get("DELETE")
        ]
        if (
            rows
            and sum(r.get("amount_cents", 0) for r in rows)
            != self.instance.amount_cents
        ):
            raise ValidationError("Allocations must total the entry amount.")


class SplitInline(admin.TabularInline):
    model = Allocation
    formset = SplitFormSet
    extra = 1
    readonly_fields = ["original_cents"]


@admin.register(Entry)
class EntryAdmin(admin.ModelAdmin):
    list_display = ["date", "description", "kind", "account", "amount_cents", "actual"]
    list_filter = ["actual", "kind", "account"]
    search_fields = ["description"]
    readonly_fields = ["original_cents", "import_key"]
    inlines = [SplitInline]


admin.site.register([Account, Category, YearPlan])
admin.site.site_header = "12and.me · Manage budget"


@admin.register(CategorySchedule)
class CategoryScheduleAdmin(admin.ModelAdmin):
    list_display = [
        "category",
        "year_plan",
        "amount_cents",
        "frequency",
        "due_month",
        "due_day",
    ]
    list_filter = ["year_plan", "frequency"]
    readonly_fields = [
        "original_amount_cents",
        "original_frequency",
        "original_due_month",
        "original_due_day",
    ]
