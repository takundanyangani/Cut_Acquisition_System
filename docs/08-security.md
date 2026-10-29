# Security

This page lists the controls in the system, what each protects against, and what remains for CUT to decide.

## Sign-in

| Control | Detail | Where |
| --- | --- | --- |
| Password hashing | Argon2id, with PBKDF2 kept only to read older hashes | `PASSWORD_HASHERS` in `config/settings.py` |
| Password rules | At least 10 characters; not similar to the user's name or email; not a common password; not all digits | `AUTH_PASSWORD_VALIDATORS` |
| Lockout | 5 failures for one account, or 20 from one IP address, inside 15 minutes block further attempts for 15 minutes. Failed signatures count too. Stored in the database, so the limit holds across every Gunicorn worker | `apps/accounts/throttle.py` |
| No account discovery | Wrong email and wrong password give the same message; password reset says the same thing whether or not the address exists; the sign-in check takes the same time for unknown accounts | `LoginForm`, `EmailOrStaffNumberBackend` |
| Temporary passwords | Accounts created in the administration site must choose a new password at first sign-in | `ForcePasswordChangeMiddleware` |
| Password reset | Single-use link, valid for two hours, sent only to active accounts with a local password | Django password reset views |
| Sign-out | Needs a POST with a CSRF token, so a link cannot sign anyone out | Django `LogoutView` |

## Sessions

- Session cookies are `HttpOnly`, `SameSite=Lax` and `Secure` in production, and scoped to the sub-path when one is set.
- Sessions end when the browser closes, after 8 hours at most, and after 30 minutes without a request.
- The CSRF cookie is `HttpOnly`. Forms carry the token. An expired form shows a plain explanation instead of a bare error.

## Signatures and the audit trail

- Signing steps ask for the password again, checked through every configured backend (local or directory). A session left open on a desk cannot approve anything.
- The signer sees a first-person statement before signing ("I, ..., signing as ICT Director, approve this purchase request..."). The statement is stored inside the audit entry.
- Each audit entry is chained to the one before by SHA-256 (see [05-architecture.md](05-architecture.md)). Entries cannot be edited or deleted through the application or the administration site. `verify_audit_chain` reports any change made directly in the database.
- Each entry records the IP address and browser of the person acting.

## Access control

- Every page and file download checks the reader's rights on the server. Hidden buttons are a convenience, not the control.
- Requests outside a user's view return 404, the same as a reference that does not exist.
- Separation of duties, independent checks and delegation rules are enforced in `workflow.capacity()`, which every action passes through. They are covered by tests in `apps/requisitions/tests/test_rules.py`.
- The administration site accepts superusers only. Workflow roles never open it. Requests, vouchers, audit entries and asset movements are read-only there, and asset custody fields cannot be edited.
- The API is read-only, needs a bearer key, stores only key hashes, and records when each key was last used.

## Uploads

| Check | Detail |
| --- | --- |
| Type | PDF, Word (`.doc`, `.docx`), Excel (`.xls`, `.xlsx`), PNG and JPEG only |
| Content | The first bytes must match the file type, so a renamed program is refused |
| Size | 10 MB per file by default (`ACQ_UPLOAD_MAX_MB`) |
| Storage | Random file names outside the static folders, created with permissions 0640 |
| Download | Through a view that checks the reader may see the request; always as a download, never shown inline; `Cache-Control: private, no-store` |
| Integrity | SHA-256 of each file is stored. Files added after submission are also logged, with the hash, in the audit trail |

The system does not scan files for viruses. If CUT runs a scanner on its servers, point it at `ACQ_UPLOAD_ROOT`.

## Browser headers

Every response carries:

- `Content-Security-Policy` allowing scripts, styles, fonts and images from the system itself only, with no inline scripts or styles. The administration site allows inline styles, which Django's admin needs.
- `X-Frame-Options: DENY` and `frame-ancestors 'none'`.
- `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`.
- `Permissions-Policy` switching off camera, microphone, location and payment.
- `Strict-Transport-Security` in production.
- `Cache-Control: private, no-cache` on pages for signed-in users.

## Concurrency

Approvals lock the request row and check the version the officer saw. Issuing stock locks each asset and checks it is still in stock. Reference numbers come from a locked counter. These were tested with parallel requests on PostgreSQL: two technicians issuing the same laptop, eight simultaneous submissions, and two actions on the same stage. In each case exactly one action won and the others got a clear message.

## Personal data

The system holds staff names, email addresses, staff numbers, job titles, departments, office locations, phone extensions, and the equipment each person holds. It does not hold national ID numbers, bank details or home addresses. Sign-in records keep IP addresses for one year.

CUT should record this processing in its register under the Cyber and Data Protection Act [Chapter 12:07] and decide how long to keep staff accounts after people leave. Deactivate leavers rather than deleting them: their names must stay on the requests and vouchers they signed.

## Secrets

- `DJANGO_SECRET_KEY`, database, SMTP and LDAP passwords live in `.env` with permissions 0600, or in the container environment. They are never in the repository.
- API keys print once at creation.

## Decisions left to CUT

1. HSTS for all `cut.ac.zw` sub-domains, and preload (`check --deploy` warnings W005 and W021).
2. Whether to keep password re-entry on every signing step (`ACQ_SIGNATURE_REQUIRES_PASSWORD`). The default is on.
3. Virus scanning of uploads.
4. Off-site copies of the weekly audit verification, to detect a rebuilt chain.
5. Retention of staff accounts and sign-in records under CUT's data protection policy.
