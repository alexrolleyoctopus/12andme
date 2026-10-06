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

All users share one household. Create trusted household logins through the admin interface; this is not a service for unrelated users. Staff users can manage accounts, years, categories and movements in Django admin. Admin monetary fields use **cents**; the main entry forms use **dollars**. Currency is AUD in this starter. Credit-card refunds are supported and reduce the assigned category’s actual spending. Positive rows in the generic CSV format still need manual classification when they are refunds or transfers.

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

The supplied credit-card export is supported directly, including its blank column, full or abbreviated month names, bank category and merchant hints. Select one credit-card account for the whole export (including both cards). Dates in the budget use the transaction date; the processing date is retained separately.

Purchases and fees become expenses. Refunds become refunds and subtract from assigned category spending. Card payments are explicitly marked as needing their source account reviewed; they do not become income or change primary cash until you match them to a planned transfer or edit them into a transfer with the correct source and destination. A card receipt date may differ from when money left your primary bank account: confirm the date against the primary account before treating its cash balance as reconciled.

Rows without a processing date are conservatively skipped until a later export includes processing information. Bank categories are hints, not automatic assignments. Use **Assign categories** to assign all previously unassigned purchases/refunds in a selected account from one bank category to one budget category. Edit individual transactions for exceptions or multiple-category splits.

The export has no bank transaction IDs. Duplicate detection uses transaction date, amount, source account/card, transaction type, description and occurrence number for otherwise identical rows. Bank category, merchant label and processing date are excluded from identity. Reimporting an unchanged file or unchanged overlapping rows is safe. Bank corrections and subsets of indistinguishable identical purchases can require manual reconciliation; no importer can reliably identify those without stable bank IDs. Full account numbers are not stored as display metadata; only the final four characters are retained, with a one-way hash used for identity.

Other bank formats can use this standard UTF-8 format:

```csv
id,date,description,amount
bank-001,2026-07-12,Registration,-1200.00
bank-002,2026-07-15,Salary,4500.00
```

Amounts use dollars, negative for outflows and positive for inflows. Dates use YYYY-MM-DD. Each row needs a stable identifier unique within its account. Reimporting the same account/ID skips the row; changed data with that ID is also skipped. Invalid rows roll back the whole upload. Maximum upload is 2 MB or 5,000 rows.

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

Before public hosting, use a production WSGI server and HTTPS proxy, configure static assets, set `DEBUG=0`, provide a strong `SECRET_KEY`, configure `ALLOWED_HOSTS`, verify HTTPS/proxy handling and static-file serving. Production settings already enable secure cookies and HTTPS redirects, and both login pages have password-guess limits. Run Django's deployment checks, arrange private persistent database storage and backups, and review deployment security. SQLite is suitable for a small household on one machine; don't put its database on a shared network filesystem.

Future work: additional bank-specific CSV mappings/preview, automatic transfer pairing, import batch undo, guided bank reconciliation, richer category trend charts, and a production hosting recipe.


## Reading and maintaining the code

Start with [the beginner's code guide](docs/CODE_GUIDE.md). The [security review](docs/SECURITY_REVIEW.md) records the review scope, fixes, verification and remaining hosting requirements.

Login attempts are limited to five failures per username or IP address, with a 15-minute cooldown. If you lock yourself out locally, run `python manage.py axes_reset`. This resets all login lockouts for the household. For long-running hosting, periodically use Axes' `axes_reset_logs` command and Django's `clearsessions` command to clean up old records; see each command's `--help` first.

Local commands create `.local-secret` automatically with owner-only access. Do not share or commit it. Changing it signs users out. WSGI startup defaults to production (`DEBUG=0`) and requires a random `SECRET_KEY` of at least 50 characters plus explicit `ALLOWED_HOSTS`. Management commands default to development unless you explicitly set `DEBUG=0`. Environment variables must be set in your shell or hosting service; `.env` files are not automatically loaded.

Optional formatting and security tools are separate from the runtime dependencies:

```sh
pip install -r requirements-dev.txt
python -m black budget config manage.py
python -m djlint templates --reformat --profile=django
python -m pip_audit -r requirements.txt
```

After pulling database changes, run `python manage.py migrate` before starting the server. The security update adds the login-attempt tables and database rules enforcing one primary account and one split per category per entry.


## Upgrades without losing your budget

Your SQLite database is separate from the application code. Updating source files does not reset it. Django records which schema migrations have been applied and `migrate` applies only the new changes, preserving existing rows unless a future migration explicitly removes or transforms them. Review migration changes and keep a backup before every upgrade.

For the current local setup:

1. Stop the running application.
2. Copy `db.sqlite3` to a private, timestamped backup location. If you configured `DATABASE_PATH`, back up that file instead.
3. Update the application code.
4. Activate the virtual environment and run `pip install -r requirements.txt`.
5. Run `python manage.py migrate` against the **same database path**.
6. Start the application and check your opening balance and recent transactions.

Do not delete the database, run `flush`, or reload demo data during an upgrade. `migrate` updates the existing database; it does not create a fresh budget. If an upgrade fails, keep the app stopped, restore the backup and restore the corresponding older application version. Do not assume old application code can safely read a newer database schema.

For future Docker hosting, place the database in a persistent volume or bind-mounted data directory outside the image and keep `DATABASE_PATH` pointed there. Rebuilding/replacing a container should replace only code. Never remove the data volume as part of an upgrade. This deployment configuration will be added when we choose the host.

The credit-card importer upgrade has an automated migration test that creates a budget under the previous schema, applies the upgrade, and verifies the accounts, categories, transactions, original budget and allocations are retained.
