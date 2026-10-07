from datetime import date
from django.contrib.auth.models import User
from django.test import Client, TestCase
from .models import Account, Allocation, Category, Entry


class TransactionCategoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("assign-test")
        self.client.force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.account = Account.objects.create(name="Card")
        self.category = Category.objects.create(name="Food")
        self.entry = Entry.objects.create(
            date=date(2025, 7, 1),
            description="Groceries",
            account=self.account,
            kind="expense",
            amount_cents=12345,
            actual=True,
            import_key="example",
        )
        self.url = f"/entry/{self.entry.pk}/category/"

    def test_assign_full_amount_and_preserve_transaction(self):
        self.assertContains(self.client.get(self.url), "Choose a category")
        response = self.client.post(self.url, {"category": self.category.pk})
        self.assertRedirects(response, "/?year=2025")
        allocation = self.entry.allocations.get()
        self.assertEqual(allocation.category, self.category)
        self.assertEqual(allocation.amount_cents, 12345)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.import_key, "example")
        self.assertEqual(self.entry.amount_cents, 12345)
        self.assertEqual(self.entry.account, self.account)
        self.client.post(self.url, {"category": self.category.pk})
        self.assertEqual(self.entry.allocations.count(), 1)

    def test_reassign_preserves_original_budget(self):
        old = Category.objects.create(name="Old")
        original = Allocation.objects.create(
            entry=self.entry, category=old, amount_cents=12345, original_cents=13000
        )
        self.client.post(self.url, {"category": self.category.pk})
        original.refresh_from_db()
        self.assertEqual(original.original_cents, 13000)
        self.assertEqual(original.amount_cents, 0)
        self.assertEqual(
            self.entry.allocations.get(category=self.category).amount_cents, 12345
        )

    def test_invalid_assignment_does_not_remove_existing(self):
        Allocation.objects.create(
            entry=self.entry, category=self.category, amount_cents=12345
        )
        for value in ("", "bad", "999999"):
            self.assertEqual(
                self.client.post(self.url, {"category": value}).status_code, 200
            )
        self.assertEqual(self.entry.allocations.get().amount_cents, 12345)

    def test_login_and_csrf_required(self):
        self.assertEqual(Client().get(self.url).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.assertEqual(
            client.post(self.url, {"category": self.category.pk}).status_code, 403
        )
        self.assertFalse(self.entry.allocations.exists())
