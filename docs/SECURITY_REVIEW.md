# Readability and security review

Reviewed 6 October 2026. Scope: the Django application in this repository, its runtime dependencies and configuration. This is a reasonable-effort code review with regression tests, not a penetration test, certification, or a review of a future internet-facing host.

## Assessment

The updated app is a reasonable starting point for testing on your own computer, bound to `127.0.0.1`. It uses Django's authentication, hashed passwords, session handling, CSRF protection, escaped HTML templates and database query API. All budget pages require a logged-in user. It remains a single shared household: every active login is trusted to read and edit the same budget. Staff/admin permissions are separate from ordinary budget access.

Do not interpret the passing tests or dependency audit as assurance against every vulnerability. Public hosting still requires a concrete deployment review.

## Findings and changes

| Finding | Impact | Resolution |
| --- | --- | --- |
| A known development secret was shared by every checkout | Undermined the confidentiality of Django's signing key if used beyond local development | Replaced with a unique local file created with owner-only permissions; ignored by Git. Production requires an explicit random secret. |
| No password-guess limit | Repeated login guesses were unrestricted | Added maintained Django Axes integration, covering the ordinary and admin login pages. Five failed attempts per username or IP trigger a 15-minute cooldown. |
| Production cookie and HTTPS safeguards were not enabled | Unsafe if the starter were exposed unchanged | WSGI now defaults to production; production settings require a secret and explicit hosts, redirect to HTTPS, secure cookies and enable one-hour HSTS. Management commands still default to local development. |
| Account URLs included unimplemented password-reset pages | Unauthenticated requests could trigger avoidable server errors | Exposed only the implemented login/logout routes. Password administration remains available to the administrator. |
| Invalid years, missing CSV fields, oversized numeric inputs and malformed match IDs could trigger errors | Could disrupt requests and expose debug information in development | Added bounded validation and readable form errors, including a CSV row limit. CSV validation happens before database writes. |
| Matching allowed income to replace an expense plan | Could invert the meaning of a real transaction and corrupt forecasts | Restricted candidates by direction and blocked rematching a transaction already attached to a plan. |
| Only form validation enforced a single primary account | Alternate write paths could create contradictory data | Added database uniqueness constraints for the primary account and entry/category split pairs. |
| Private pages had no explicit cache policy | Browser/intermediary caching of budget pages was not discouraged | Added `no-store` response headers to non-static pages. |
| Dense one-line code and templates | Made learning, maintenance and review unnecessarily difficult | Expanded Python, HTML and CSS, added explanations, separated CSV parsing from page handlers, split reporting into named calculation steps and added a code guide. |
| Display calculations converted cents to floating-point dollars | Avoidable precision ambiguity | Retained exact Decimal values through report formatting. |

## Verification

- Automated tests cover existing budget behaviour plus authentication requirements, CSRF rejection on writes, read-only GET behaviour, HTML escaping, cache/security headers, hostile Host headers, CSV validation/size limits, invalid form input, transaction matching, database constraints, secret generation, login limits on both login pages, logout and external redirect rejection.
- Runtime dependencies were audited using `pip-audit -r requirements.txt --no-deps --disable-pip`. All runtime dependencies, including transitive Django dependencies, are explicitly pinned. The audit reported **no known vulnerabilities** at review time. This is advisory-database coverage, not proof that packages are vulnerability-free.
- Django's normal system checks and migration consistency check were run.
- Django deployment checks were run with production settings and a temporary random secret. Two warnings remain intentionally: `SECURE_HSTS_INCLUDE_SUBDOMAINS` and `SECURE_HSTS_PRELOAD`. Those require ownership/HTTPS decisions for the real domain and all its subdomains; they should not be enabled blindly to remove warnings.
- Reviewed application code for raw SQL assembled from input, command execution, dynamic evaluation, CSRF exemptions and template escaping bypasses. None were found in application request handling.

## Before internet exposure

1. Use a production WSGI server and HTTPS proxy; never expose `runserver`. Configure the actual allowed hostname and a separate private production secret. Serve collected static assets without exposing the database, repository, local secret or backups.
2. Verify the proxy's handling of HTTPS and client IPs. The app deliberately ignores client-supplied forwarding headers. Behind a proxy, all requests may currently share the proxy's IP for lockout purposes. Configure trusted proxy handling only after choosing the host, and prevent direct access around it.
3. Cap the total HTTP request body size at the proxy. The app's 2 MB CSV and 5,000-row limits bound parsing/import work; they do not prevent a malicious client from transmitting a larger multipart body before the application rejects it.
4. Use real household accounts and strong unique passwords. The separately created localhost preview account uses publicly documented demo credentials and must never be reused in a hosted or real-data database.
5. Protect and back up the database and server. SQLite does not encrypt this data by itself. The app does not defend against someone who can read your computer's files or compromise your administrator account.
6. Keep Python and pinned dependencies updated, rerun the audit periodically, and manage logs/expired sessions. Axes stores failed login attempts in SQLite; high-volume attacks still need protection at the host/proxy layer. Lockouts can also temporarily block legitimate users.

## Remaining functional limitations

CSV transfer pairing, import batch undo and guided reconciliation remain starter limitations described in README. Review imported transactions before relying on financial totals. Concurrent edits have no conflict-resolution interface, and there is no separate immutable audit trail of every edit. Original budget amounts are retained for ordinary amount edits, but moving a planned entry to a different month or changing its kind/account changes where that original budget appears; a fully versioned annual budget is not implemented. These limits matter for data integrity even though they are not login bypasses.

## References

- [Django security overview](https://docs.djangoproject.com/en/5.2/topics/security/)
- [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)
- [Django Axes installation](https://django-axes.readthedocs.io/en/latest/2_installation.html)
- [Django Axes configuration](https://django-axes.readthedocs.io/en/latest/4_configuration.html)


## Home-server deployment addition — 7 October 2026

The repository now includes Docker Compose, Gunicorn, WhiteNoise and Caddy local HTTPS. The app runs as non-root with a read-only container filesystem and a persistent writable data mount; its port is not published. Only Caddy publishes ports, bound to the configured LAN address. Caddy limits bodies to 3 MB and overwrites the scheme and dedicated client-IP headers before forwarding. `TRUST_CADDY=1` opts into trusting those headers only for the supplied private-network deployment. Keep the app port private; enabling this setting on a directly exposed app would allow header spoofing.

The original proxy/body-limit follow-ups above are addressed by this configuration, but must still be verified on the actual server. Docker was unavailable in the authoring environment, so no container build, image vulnerability scan or live Caddy/TLS integration test was performed there. See `DOCKER.md` for server-side validation and certificate trust. Local Python tests and runtime-dependency audits do not cover the base operating-system or Caddy image.
