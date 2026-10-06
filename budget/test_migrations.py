"""Prove the card-import schema upgrade preserves existing household records."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CardUpgradeTests(TransactionTestCase):
    def test_existing_budget_survives_card_import_upgrade(self):
        old = [
            (
                "budget",
                "0002_alter_yearplan_closed_through_alter_yearplan_year_and_more",
            )
        ]
        new = [("budget", "0003_entry_bank_category_entry_bank_merchant_and_more")]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            account = apps.get_model("budget", "Account").objects.create(
                name="Existing bank", primary=True
            )
            category = apps.get_model("budget", "Category").objects.create(
                name="Existing food", account=account
            )
            year = apps.get_model("budget", "YearPlan").objects.create(
                year=2026, opening_cents=123456
            )
            entry = apps.get_model("budget", "Entry").objects.create(
                date="2026-07-01",
                description="Existing purchase",
                kind="expense",
                account=account,
                amount_cents=4567,
                original_cents=5000,
                actual=True,
            )
            allocation = apps.get_model("budget", "Allocation").objects.create(
                entry=entry, category=category, amount_cents=4567, original_cents=5000
            )
            executor = MigrationExecutor(connection)
            executor.migrate(new)
            apps = executor.loader.project_state(new).apps
            saved = apps.get_model("budget", "Entry").objects.get(pk=entry.pk)
            self.assertEqual(saved.description, "Existing purchase")
            self.assertEqual(saved.amount_cents, 4567)
            self.assertEqual(saved.original_cents, 5000)
            self.assertTrue(saved.actual)
            self.assertEqual(saved.account_id, account.pk)
            self.assertEqual(saved.bank_category, "")
            self.assertEqual(
                apps.get_model("budget", "YearPlan")
                .objects.get(pk=year.pk)
                .opening_cents,
                123456,
            )
            self.assertEqual(
                apps.get_model("budget", "Allocation")
                .objects.get(pk=allocation.pk)
                .category_id,
                category.pk,
            )
        finally:
            MigrationExecutor(connection).migrate(new)
