"""Regression tests for security boundaries and untrusted input."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase

from config.security import client_ip, secret_key
from .forms import YearForm
from .models import Account, Allocation, Category, Entry, YearPlan


class SecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tester", password="test-password-123")
        self.account = Account.objects.create(name="Bank", primary=True)
        self.category = Category.objects.create(name="Food", account=self.account)
        self.plan = YearPlan.objects.create(year=2026)
        self.entry = Entry.objects.create(
            date="2026-07-01",
            description="Planned food",
            kind="expense",
            account=self.account,
            amount_cents=10000,
        )

    def login(self, client=None):
        (client or self.client).force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )

    def test_every_budget_page_requires_login(self):
        paths = [
            "/",
            "/add/account/",
            "/add/category/",
            "/add/year/",
            "/add/entry/",
            "/import/",
            f"/entry/{self.entry.pk}/edit/",
            f"/entry/{self.entry.pk}/match/",
            f"/entry/{self.entry.pk}/settle/",
        ]
        for path in paths:
            for method in [self.client.get, self.client.post]:
                with self.subTest(path=path, method=method.__name__):
                    self.assertEqual(method(path).status_code, 302)

    def test_writes_reject_missing_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        self.login(client)
        for path in [
            "/add/account/",
            "/add/entry/",
            "/import/",
            f"/entry/{self.entry.pk}/edit/",
            f"/entry/{self.entry.pk}/settle/",
            f"/entry/{self.entry.pk}/match/",
        ]:
            with self.subTest(path=path):
                self.assertEqual(client.post(path).status_code, 403)
        self.entry.refresh_from_db()
        self.assertFalse(self.entry.actual)

    def test_get_cannot_mark_actual(self):
        self.login()
        self.assertEqual(
            self.client.get(f"/entry/{self.entry.pk}/settle/").status_code, 405
        )
        self.entry.refresh_from_db()
        self.assertFalse(self.entry.actual)

    def test_html_in_description_is_escaped_and_page_not_cached(self):
        self.entry.description = '<script>alert("xss")</script>'
        self.entry.save()
        self.login()
        response = self.client.get("/?year=2026")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("xss")</script>')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_untrusted_host_is_rejected(self):
        self.assertEqual(
            self.client.get("/", HTTP_HOST="evil.example").status_code, 400
        )

    def test_empty_year_form_and_huge_year_parameter_do_not_crash(self):
        self.assertFalse(YearForm({}).is_valid())
        self.login()
        self.assertEqual(self.client.get("/?year=" + "9" * 100).status_code, 200)
        self.assertEqual(self.client.post("/add/year/", {}).status_code, 200)

    def test_bad_csv_and_unknown_account_are_form_errors(self):
        self.login()
        for row in [
            "id,date,description,amount\nx,2026-07-01,Food\n",
            "id,date,description,amount\nx,2026-07-01,Food,NaN\n",
            "id,date,description,amount\nx,2026-07-01,Food,1e999999\n",
            "id,id,date,description,amount\nx,x,2026-07-01,Food,1\n",
        ]:
            response = self.client.post(
                "/import/",
                {
                    "account": self.account.pk,
                    "file": SimpleUploadedFile("bank.csv", row.encode()),
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        response = self.client.post("/import/", {"account": "invalid"})
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(Entry.objects.count(), 1)

    def test_large_upload_is_rejected(self):
        self.login()
        response = self.client.post(
            "/import/",
            {
                "account": self.account.pk,
                "file": SimpleUploadedFile("bank.csv", b"x" * (2 * 1024 * 1024 + 1)),
            },
        )
        self.assertContains(response, "Maximum CSV size")

    def test_income_cannot_be_matched_to_expense(self):
        actual = Entry.objects.create(
            date="2026-07-01",
            description="Salary",
            kind="income",
            account=self.account,
            amount_cents=10000,
            actual=True,
            import_key="income",
        )
        self.login()
        response = self.client.post(
            f"/entry/{actual.pk}/match/", {"planned": self.entry.pk}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Entry.objects.count(), 2)

    def test_invalid_match_identifier_does_not_crash(self):
        actual = Entry.objects.create(
            date="2026-07-01",
            description="Food",
            kind="expense",
            account=self.account,
            amount_cents=10000,
            actual=True,
            import_key="food",
        )
        self.login()
        self.assertEqual(
            self.client.post(
                f"/entry/{actual.pk}/match/", {"planned": "bad"}
            ).status_code,
            404,
        )

    def test_already_matched_transaction_cannot_replace_another_plan(self):
        self.entry.actual = True
        self.entry.import_key = "already-matched"
        self.entry.save()
        self.login()
        self.assertEqual(
            self.client.post(
                f"/entry/{self.entry.pk}/match/", {"planned": self.entry.pk}
            ).status_code,
            302,
        )
        self.assertTrue(Entry.objects.filter(pk=self.entry.pk).exists())

    def test_regular_user_cannot_open_admin(self):
        self.login()
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_login_throttles_correct_password_after_five_failures(self):
        for _ in range(5):
            response = self.client.post(
                "/accounts/login/", {"username": "tester", "password": "wrong"}
            )
        self.assertEqual(response.status_code, 429)
        response = self.client.post(
            "/accounts/login/", {"username": "tester", "password": "test-password-123"}
        )
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admin_login_is_throttled_too(self):
        for _ in range(5):
            response = self.client.post(
                "/admin/login/", {"username": "tester", "password": "wrong"}
            )
        self.assertEqual(response.status_code, 429)

    def test_forwarded_ip_header_is_not_trusted(self):
        from django.test import RequestFactory

        request = RequestFactory().get(
            "/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4"
        )
        self.assertEqual(client_ip(request), "127.0.0.1")

    def test_external_login_redirect_is_rejected(self):
        response = self.client.post(
            "/accounts/login/?next=https://evil.example",
            {"username": "tester", "password": "test-password-123"},
        )
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_logout_invalidates_session(self):
        self.login()
        self.client.post("/accounts/logout/")
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_database_prevents_multiple_primary_accounts(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(name="Other", primary=True)

    def test_database_prevents_duplicate_category_splits(self):
        Allocation.objects.create(
            entry=self.entry, category=self.category, amount_cents=10000
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Allocation.objects.create(
                entry=self.entry, category=self.category, amount_cents=10000
            )

    def test_local_secret_is_private_stable_and_not_shared(self):
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            with patch.dict(os.environ, {"SECRET_KEY": ""}):
                key = secret_key(first, debug=True)
                self.assertEqual(key, secret_key(first, debug=True))
                self.assertNotEqual(key, secret_key(second, debug=True))
                self.assertGreaterEqual(len(key), 50)
                self.assertEqual(
                    Path(first, ".local-secret").stat().st_mode & 0o777, 0o600
                )
                with self.assertRaises(ImproperlyConfigured):
                    secret_key(first, debug=False)

    def test_weak_production_secret_is_rejected(self):
        with patch.dict(os.environ, {"SECRET_KEY": "short"}):
            with self.assertRaises(ImproperlyConfigured):
                secret_key("/tmp", debug=False)

    def test_unimplemented_password_reset_is_not_exposed(self):
        self.assertEqual(self.client.get("/accounts/password_reset/").status_code, 404)

    def test_out_of_range_date_is_rejected_before_recurrence(self):
        self.login()
        response = self.client.post(
            "/add/entry/",
            {
                "date": "9999-12-31",
                "description": "Pay",
                "kind": "income",
                "account": self.account.pk,
                "amount": "100",
                "repeat": "fortnightly",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(Entry.objects.count(), 1)
