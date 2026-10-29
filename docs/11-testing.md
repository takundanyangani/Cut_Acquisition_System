# Testing

## Automated tests

```bash
python manage.py test
```

`manage.py test` switches to `config/settings_test.py` (fast password hashing, email kept in memory, temporary upload folder). The suite has 129 tests and runs on SQLite in about 8 seconds and on PostgreSQL in about 20.

```bash
DATABASE_URL=postgres://user:password@localhost:5432/cutacq python manage.py test
```

| Area | File | What it proves |
| --- | --- | --- |
| Routes | `apps/requisitions/tests/test_workflow.py` | Every step of CUT ICT 38, 40 and 42; stock instead of purchase; partial delivery; disputes and withdrawals; conversion of allocation requests; return to the right reviewer; gap-free numbering |
| Controls | `apps/requisitions/tests/test_rules.py` | No self-approval; delegation with both names recorded; expired, revoked and orphaned delegations; independent checks; wrong passwords refused and counted toward lockout; stale forms refused; cancellation limits; archived requests read-only; queues |
| Pages | `apps/requisitions/tests/test_views.py` | Forms, file checks, edits after return, 404 for other people's requests, attachment download rights, Excel export, action pages including vouchers, receipt and delivery |
| Audit | `apps/requisitions/tests/test_audit_and_commands.py` | Chain intact; edits and deletes refused; tampering and missing entries detected; reminders once a day; retention; the demo seed runs the whole workflow |
| Sign-in | `apps/accounts/tests/test_auth.py` | Email and staff number sign-in; lockout by account and by IP; expiry; forced password change; idle timeout; password reset; delegation pages; administration site limited to superusers |
| Directory | `apps/accounts/tests/test_ldap_backend.py` | Account matching by mail (skipped when the optional LDAP packages are absent) |
| Assets | `apps/assets/tests/test_assets.py` | Tags, duplicate serials, manual status rules, append-only movements, register pages, voucher retention, CSV imports |
| API | `apps/api/tests/test_api.py` | Keys required, revoked keys refused, read-only, drafts hidden, filters |
| Core | `apps/core/tests/test_core.py` | Working days and holidays, counters, outbox send and retry, safe notification links, custom 404 and 500 pages, CSRF page, security headers, health check |

Lint and formatting:

```bash
ruff check . && ruff format --check .
```

## Browser test

`e2e/full-route.js` drives one equipment request through every step in Chromium: the requester (with a second item row added and a third removed), the Personal Advisor, the ICT Director (a wrong password first, then the right one), the CSM, the technician, the Operations Manager, the Director's approval, Procurement, delivery of two laptops, the voucher, and the requester's signature. It then checks the printed record, the profile, the mobile menu and the theme switch, and runs axe-core on 23 pages.

```bash
python manage.py seed_demo
```

```bash
cd e2e && npm install && BASE=http://127.0.0.1:8000 npm test
```

It fails on any browser console error or accessibility violation.

## Results at handover (29 October 2026)

| Check | Result |
| --- | --- |
| Automated tests on SQLite and PostgreSQL 16 | 129 passed |
| Browser test against Gunicorn with production settings (`DEBUG` off, hashed static files) | Passed; no console errors |
| axe-core, WCAG 2.1 A and AA plus best practice, 23 pages in light mode and 8 in dark mode | No violations |
| Colour contrast of every text and control colour pair, both themes | All at least 4.5:1 for text and 3:1 for controls |
| Layout at 375, 768, 1024 and 1440 pixels wide, 27 pages including request, print, voucher and asset pages | No horizontal scrolling |
| `check --deploy` with production settings | Only the HSTS sub-domain and preload warnings, which are CUT's decision |
| Parallel actions on PostgreSQL (threads with a barrier) | Double issue of one laptop: one voucher, one refusal. Eight simultaneous submissions: eight unique, consecutive numbers. Two actions on one stage: one applied, one told the request had changed |
| Directory sign-in against OpenLDAP 2.6 | Sign-in by email and by user ID matched the same account; roles kept; first-time account created; signatures checked against the directory; local accounts still worked |
| Sub-path hosting (`DJANGO_SCRIPT_NAME=/ict-acquisitions`) | Links, redirects, static files and cookie paths carried the prefix |
| Audit chains after the demo seed, the browser test and the parallel tests | All intact |

## Not tested here

- The Docker image. The development environment had no Docker daemon. The Dockerfile runs the same steps that were tested on the server install (install requirements, `collectstatic`, Gunicorn). Build and run it once on a staging server.
- CUT's mail server and Active Directory. Email was tested with Django's console and in-memory backends; LDAP with OpenLDAP. Try both against CUT's real services on staging.
- Load. No load test was run. A university ICT office produces tens of requests a week; each page needs a handful of database queries.
