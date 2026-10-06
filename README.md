# 12and.me

A private, single-household budgeting app built with Django 5.2, SQLite and HTML/CSS. Plan January–December, forecast the primary transaction account, and compare category spending with the original budget.

## Start locally

Requires Python 3.10 or newer (3.12 recommended). macOS's built-in Python may be too old.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000 and sign in. A virtual environment is already prepared in this checkout; use `.venv/bin/python manage.py createsuperuser` and `.venv/bin/python manage.py runserver` to start here.

Optional fictional sample data, before adding any budget data:

```sh
python manage.py demo
```

The demo uses the current calendar year and includes monthly salary, fortnightly pay, card funding split between food and fuel, spending allowances, and registration in July. It does not create a password or user.

## Set up your household

1. Add accounts and tick **primary** for your everyday transaction account. Only one account may be primary.
2. Add a budget year with the primary account's 1 January opening balance, in dollars. Leave **closed through** at 0 initially.
3. Add categories with their usual payment account.
4. Add planned money movements. Income uses its receiving account; expenses and transfers use their paying account. Transfers also need a destination.
5. For recurring movements choose monthly, quarterly or fortnightly through December. Annual bills are entered once in their due month. Individual occurrences can be edited independently.
6. Enter category splits as `Food: 1000.00`, one line per category. They must total the movement amount. Select the payment account explicitly; a category's usual account is descriptive in this version.

All users share one household. Create trusted household logins through the admin interface; this is not a service for unrelated users. Staff users can manage accounts, years, categories and movements in Django admin. Admin monetary fields use **cents**; the main entry forms use **dollars**. Currency is AUD in this starter. Refunds currently import as income; netting refunds against category spending is future work.

## How the calculations work

- Opening primary cash + incoming movements − outgoing movements = closing cash.
- Closing cash becomes next month's opening cash.
- A transfer to a credit card reduces primary cash immediately. Card purchases count as category spending, without reducing primary cash again.
- Transfers can have category funding splits; those splits are excluded from spending totals.
- Income less spending covers all tracked accounts and excludes transfers. It is distinct from the primary account's monthly cash difference.
- Original planned amounts are preserved when amounts are edited or a plan becomes actual. Category cells show original budget, actual, remaining original budget and current forecast.
- For a spending bucket, add one planned monthly allowance and categorise individual actual purchases to the same account and category. Unmatched actual purchases consume that allowance automatically; overspending raises the forecast above it.
- For an individual bill or transfer, edit the planned movement to the real amount and **mark actual**, or match an imported transaction to it. Do not both mark a plan actual and retain a separate imported copy.
- Completed months (configured using **closed through** on the year) use actuals only. Open months use actuals plus remaining plans. Closing a month requires checking completeness against the bank balance yourself.
- Years are independent: enter the previous year's closing primary balance as the next year's opening balance.

The month-end forecast does not detect a shortfall between paydays inside a month.

## CSV imports

The starter accepts a standard UTF-8 format; transform your bank export to these columns first. Bank-specific column mapping and an import preview are future work.

```csv
id,date,description,amount
bank-001,2026-07-12,Registration,-1200.00
bank-002,2026-07-15,Salary,4500.00
```

Amounts use dollars, negative for outflows and positive for inflows. Dates use YYYY-MM-DD. Each row needs a stable identifier unique within its account. Reimporting the same account/ID skips the row; changed data with that ID is also skipped. Invalid rows roll back the whole upload. Maximum upload is 2 MB.

Imported entries initially have no categories. Use **Edit** to categorise bucket purchases or identify transfers. Use **Match plan** for individual planned payments, salaries and transfers. If split amounts differ, adjust the planned entry and splits first, then match. Matching keeps the bank date, actual amount, import identifier, original budget and planned categories.

**Important:** Transfers imported from both accounts are two bank records for one movement. This starter does not automatically pair those records. Keep one transfer with source and destination and remove the duplicate counterpart in Manage. Otherwise income/cash totals can be overstated. Review imported entries before relying on the forecast. Import batch undo is not implemented; individual entries can be removed in Manage.

## Back up and restore

The live database and exports must stay out of Git. `.gitignore` excludes SQLite databases, `.env`, and `data/`.

Stop the application before copying `db.sqlite3` to a private backup location. Restore with the application stopped, by replacing the database with that backup. Keep periodic backups away from this machine and test restoration. For backups while the app is running, use SQLite's backup API rather than copying a live file.

## Tests

```sh
python manage.py test
python manage.py check
```

## Hosting

This foundation is intended for localhost/private development. The development server is not an internet deployment server. Docker is optional and is not required to start.

Before public hosting, use a production WSGI server and HTTPS proxy, configure static assets, set `DEBUG=0`, provide a strong `SECRET_KEY`, configure `ALLOWED_HOSTS`, enable secure cookies and HTTPS redirects, and add login throttling. Run Django's deployment checks, arrange private persistent database storage and backups, and review deployment security. SQLite is suitable for a small household on one machine; don't put its database on a shared network filesystem.

Future work: bank-specific CSV mappings/preview, automatic transfer pairing, import batch undo, guided bank reconciliation, richer category trend charts, and a production hosting recipe.
