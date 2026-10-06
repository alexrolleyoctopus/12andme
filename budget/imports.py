"""Validate a small CSV completely, then save all its rows in one transaction."""

import csv
import hashlib
import io
from datetime import date
from collections import Counter
import json
from decimal import Decimal, InvalidOperation
from django.db import transaction
from .models import Entry

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_AMOUNT = Decimal("9999999999.99")
REQUIRED_COLUMNS = {"id", "date", "description", "amount"}
CARD_COLUMNS = {
    "Date",
    "Amount",
    "Account Number",
    "Transaction Type",
    "Transaction Details",
    "Category",
    "Merchant Name",
    "Processed On",
}


def card_date(value):
    """Accept the export's mix of short/full English months, including 'Sept'."""
    month_names = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
    months = {name: number for number, name in enumerate(month_names, start=1)}
    months.update({name[:3]: number for number, name in enumerate(month_names, start=1)})
    months["sept"] = 9
    day, month, year = value.strip().lower().split()
    if month not in months or len(year) != 2 or not year.isdigit():
        raise ValueError("Expected a date such as 30 Sept 26.")
    return date(2000 + int(year), months[month], int(day))


def normalise_card_row(row, occurrences):
    """Convert the bank's columns to our standard internal import representation."""
    payment_date = card_date(row["Date"])
    processed_date = (
        card_date(row["Processed On"]) if row["Processed On"].strip() else None
    )
    amount = Decimal(row["Amount"])
    transaction_type = row["Transaction Type"].strip()
    kinds = {
        "CREDIT CARD PURCHASE": "expense",
        "FEES": "expense",
        "MISCELLANEOUS DEBIT": "expense",
        "CREDIT CARD REFUND": "refund",
        "CREDIT CARD PAYMENT": "card_payment",
    }
    if transaction_type not in kinds:
        raise ValueError("Unrecognised card transaction type.")
    kind = kinds[transaction_type]
    if (
        not amount.is_finite()
        or abs(amount) > MAX_AMOUNT
        or (kind == "expense" and amount >= 0)
        or (kind != "expense" and amount <= 0)
    ):
        raise ValueError("Unexpected sign for this card transaction type.")
    # The bank provides no transaction ID. Exclude mutable category/merchant hints
    # and processing date so editing those does not create a duplicate transaction.
    identity = json.dumps(
        [
            str(payment_date),
            str(amount.normalize()),
            row["Account Number"].strip(),
            transaction_type,
            row["Transaction Details"].strip(),
        ],
        ensure_ascii=False,
    )
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()
    if processed_date is not None:
        occurrences[fingerprint] += 1
    source = row["Account Number"].strip()
    return {
        "id": f"card-v1:{fingerprint}:{occurrences[fingerprint]}",
        "date": str(payment_date),
        "amount": str(amount),
        "description": row["Transaction Details"],
        "kind": kind,
        "pending": processed_date is None,
        "metadata": {
            "bank_category": row["Category"][:200],
            "bank_merchant": row["Merchant Name"][:200],
            "bank_type": transaction_type[:80],
            "source_label": f"Ending {source[-4:]}" if source else "",
            "processed_date": processed_date,
        },
    }


def read_rows(upload):
    if upload.size > MAX_BYTES:
        raise ValueError("Maximum CSV size is 2 MB.")
    try:
        text = upload.read(MAX_BYTES + 1).decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("Save the CSV using UTF-8 encoding.") from None
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Maximum CSV size is 2 MB.")
    reader = csv.DictReader(io.StringIO(text), strict=True)
    try:
        headers = reader.fieldnames or []
        is_card = CARD_COLUMNS.issubset(headers)
        required = CARD_COLUMNS if is_card else REQUIRED_COLUMNS
        if not required.issubset(headers) or len(headers) != len(set(headers)):
            raise ValueError(
                "Use the supported credit-card export, or unique id,date,description,amount columns."
            )
        rows = []
        occurrences = Counter()
        for number, row in enumerate(reader, start=2):
            if len(rows) >= MAX_ROWS:
                raise ValueError("Import at most 5,000 transactions at a time.")
            if None in row or any(row.get(column) is None for column in required):
                raise ValueError(f"Row {number}: missing or extra cells.")
            if is_card:
                try:
                    if len(row["Amount"].strip()) > 30:
                        raise ValueError("Amount is too long.")
                    row = normalise_card_row(row, occurrences)
                except (ValueError, InvalidOperation) as error:
                    raise ValueError(
                        f"Row {number}: invalid card transaction ({error})."
                    ) from None
            else:
                # Extra standard-CSV columns must not override internal metadata.
                row = {column: row[column] for column in REQUIRED_COLUMNS}
            bank_id = row["id"].strip()
            if not bank_id or len(bank_id) > 200:
                raise ValueError(
                    f"Row {number}: use a transaction ID of 1–200 characters."
                )
            try:
                amount_text = row["amount"].strip()
                if len(amount_text) > 30:
                    raise ValueError()
                amount = Decimal(amount_text)
                if not amount.is_finite() or amount == 0 or abs(amount) > MAX_AMOUNT:
                    raise ValueError()
                if amount != amount.quantize(Decimal("0.01")):
                    raise ValueError()
                payment_date = date.fromisoformat(row["date"])
                if not 2000 <= payment_date.year <= 2100:
                    raise ValueError()
            except (ValueError, InvalidOperation):
                raise ValueError(
                    f"Row {number}: use a valid date (2000–2100) and nonzero amount with at most two decimal places."
                ) from None
            rows.append(
                {
                    "bank_id": bank_id,
                    "date": payment_date,
                    "description": row["description"][:200] or "Imported transaction",
                    "amount": amount,
                    "kind": row.get("kind", "income" if amount > 0 else "expense"),
                    "pending": row.get("pending", False),
                    "metadata": row.get("metadata", {}),
                }
            )
    except csv.Error:
        raise ValueError(
            "The CSV is malformed or contains an excessively long field."
        ) from None
    return rows


@transaction.atomic
def save_rows(account, rows):
    if any(row["metadata"] for row in rows) and account.primary:
        raise ValueError(
            "Choose a separate credit-card account for this export, not the primary transaction account."
        )
    added = 0
    for row in rows:
        # A missing processing date is not reliable evidence of settled spending.
        if row["pending"]:
            continue
        key = hashlib.sha256(f"{account.pk}:{row['bank_id']}".encode()).hexdigest()
        amount = row["amount"]
        _, created = Entry.objects.get_or_create(
            import_key=key,
            defaults={
                "date": row["date"],
                "description": row["description"],
                "kind": row["kind"],
                "account": account,
                "amount_cents": int(abs(amount) * 100),
                "actual": True,
                **row["metadata"],
            },
        )
        added += created
    return added
