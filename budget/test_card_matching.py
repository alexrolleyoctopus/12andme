from datetime import date
from django.contrib.auth.models import User
from django.test import TestCase
from .models import Account, Entry, YearPlan
from .services import report


class CardMatchingTests(TestCase):
    def setUp(self):
        self.client.force_login(
            User.objects.create_user("card-match"),
            backend="django.contrib.auth.backends.ModelBackend",
        )
        self.bank = Account.objects.create(name="Bank", primary=True)
        self.card = Account.objects.create(name="Card", account_type="credit_card")
        self.plan = YearPlan.objects.create(year=2026, opening_cents=100000)
        self.out = Entry.objects.create(
            date=date(2026, 7, 31),
            description="Pay card",
            account=self.bank,
            kind="expense",
            amount_cents=10000,
            actual=True,
            import_key="bank-key",
        )
        self.receipt = Entry.objects.create(
            date=date(2026, 8, 2),
            description="Payment received",
            account=self.card,
            kind="card_payment",
            amount_cents=10000,
            actual=True,
            import_key="card-key",
        )

    def test_classify_match_and_cash(self):
        response = self.client.post(
            "/transactions/?month=2026-07",
            {f"entry_{self.out.pk}-card_destination": self.card.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.out.refresh_from_db()
        self.assertEqual(self.out.kind, "transfer")
        response = self.client.post(
            f"/entry/{self.receipt.pk}/match-card/", {"transfer": self.out.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.matched_transfer, self.out)
        self.assertEqual(self.receipt.import_key, "card-key")
        self.out.refresh_from_db()
        self.assertEqual(self.out.import_key, "bank-key")
        self.assertEqual(self.out.date, date(2026, 7, 31))
        months, _, _ = report(self.plan)
        self.assertEqual(months[6]["closing"], 900)
        self.assertEqual(months[7]["closing"], 900)
        self.assertEqual(months[6]["spending_net"], 0)
        self.assertEqual(months[7]["spending_net"], 0)
        self.client.post(f"/entry/{self.out.pk}/edit/", {})
        self.out.refresh_from_db()
        self.assertEqual(self.out.amount_cents, 10000)

    def test_mismatched_amount_rejected(self):
        self.out.kind = "transfer"
        self.out.destination = self.card
        self.out.amount_cents = 9999
        self.out.save()
        response = self.client.post(
            f"/entry/{self.receipt.pk}/match-card/", {"transfer": self.out.pk}
        )
        self.assertEqual(response.status_code, 404)
        self.receipt.refresh_from_db()
        self.assertIsNone(self.receipt.matched_transfer_id)

    def test_account_type_can_be_edited(self):
        savings = Account.objects.create(name="Savings")
        self.client.post(
            f"/account/{savings.pk}/edit/",
            {"name": "Savings", "account_type": "savings"},
        )
        savings.refresh_from_db()
        self.assertEqual(savings.account_type, "savings")
