# Integration with CUT systems

The system can run on its own, but it is built to join CUT's existing services. This page covers six joins: staff directory sign-in, hosting inside the CUT portal, the read-only API, data imports, Excel exports and email.

## 1. Sign-in with the staff directory (LDAP or Active Directory)

Staff can sign in with their university password instead of a separate one. Roles stay in this system.

Install the optional packages first. `python-ldap` builds against the OpenLDAP and SASL headers:

```bash
sudo apt install libldap-dev libsasl2-dev
```

```bash
.venv/bin/pip install -r requirements-ldap.txt
```

Then add the directory settings to `.env`:

```
LDAP_SERVER_URI=ldaps://dc1.cut.ac.zw
LDAP_BIND_DN=CN=svc-acquisitions,OU=Service Accounts,DC=cut,DC=ac,DC=zw
LDAP_BIND_PASSWORD=...
LDAP_USER_BASE_DN=OU=Staff,DC=cut,DC=ac,DC=zw
LDAP_USER_FILTER=(|(mail=%(user)s)(employeeID=%(user)s))
```

The names above are examples; use the values CUT's directory team gives you. `LDAP_USER_FILTER` decides what staff may type. The example accepts the email address or the employee ID attribute, so staff numbers work too.

`apps/accounts/ldap_backend.py` finds the local account by the directory's `mail` attribute, whatever the person typed. An account imported earlier with `import_users` keeps its roles. A person signing in for the first time gets a new local account with the name and email from the directory and no local password. Local accounts still work after LDAP is switched on, which keeps a break-glass administrator available if the directory is down.

Signatures check the password through the same backends, so a directory user signs with their directory password. Password reset in this system does not apply to directory accounts; they reset through the ICT Helpdesk as usual.

The backend was tested against OpenLDAP 2.6: sign-in by email and by `uid`, matching to an existing account with roles, first-time account creation, wrong-password refusal, signature checks, and local fallback. It has not been tested against CUT's Active Directory; do that on a staging server before go-live.

## 2. Inside the CUT portal

To serve the system under a path such as `https://portal.cut.ac.zw/ict-acquisitions/`:

1. Set `DJANGO_SCRIPT_NAME=/ict-acquisitions` and `ACQ_SITE_URL=https://portal.cut.ac.zw`.
2. Proxy the path to Gunicorn and strip the prefix:

```nginx
location /ict-acquisitions/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Links, redirects, static files, email links and cookie paths then carry the prefix. Cookies are scoped to the path, so they do not leak to other portal applications.

The pages send `X-Frame-Options: DENY` and `frame-ancestors 'none'`, so the system cannot be shown inside a frame. Link to it from the portal instead.

## 3. Read-only JSON API

Other systems (the ERP, the current asset management system, a portal widget) can read requests, assets and vouchers. The API cannot change anything.

Create one key per system, on the server. The key prints once; only its hash is stored:

```bash
.venv/bin/python manage.py create_api_key "ERP integration"
```

Revoke a key in the administration site: API keys, then the Revoke action.

Send the key as a bearer token:

```bash
curl -H "Authorization: Bearer cutacq_1a2b3c4d_..." https://acquisitions.cut.ac.zw/api/v1/requests/?state=with_procurement
```

The endpoints:

| Method and path | Returns | Filters |
| --- | --- | --- |
| `GET /api/v1/requests/` | Submitted requests, oldest first | `state`, `type` (`ALC`, `EQR`, `RPL`), `updated_since` (ISO 8601), `page`, `page_size` (max 200) |
| `GET /api/v1/requests/<reference>/` | One request with items, voucher numbers and the routing slip with hashes | |
| `GET /api/v1/assets/` | The asset register | `status`, `updated_since`, `page`, `page_size` |
| `GET /api/v1/assets/<asset_tag>/` | One asset | |
| `GET /api/v1/vouchers/<number>/` | One issue voucher with its lines and the signed statement | |
| `GET /healthz` | `{"status": "ok"}` | No key needed |

Lists look like this:

```json
{
  "count": 42,
  "page": 1,
  "pages": 1,
  "results": [
    {
      "reference": "EQR-2026-0042",
      "type": "EQR",
      "type_label": "Equipment request",
      "procedure": "CUT ICT 38",
      "title": "Workstations for the CAD teaching lab",
      "state": "with_procurement",
      "state_label": "With Procurement",
      "with": "Procurement, followed up by Kudakwashe Dube",
      "requester": {"name": "Tendai Moyo", "email": "tendai.moyo@cut.ac.zw", "staff_number": "CUT1001"},
      "department": "Engineering",
      "location": "Main Campus, Engineering Block, Room 10",
      "priority": "urgent",
      "procurement_reference": "PR-2026-1183",
      "submitted_at": "2026-10-20T10:12:00+00:00",
      "approved_at": "2026-10-28T09:40:00+00:00",
      "sent_to_procurement_at": "2026-10-28T14:05:00+00:00",
      "closed_at": null,
      "archived": false,
      "updated_at": "2026-10-28T14:05:00+00:00"
    }
  ]
}
```

Errors come back as `{"error": "..."}` with status 400, 401, 404 or 405. Drafts never appear.

To keep another system in step, poll `/api/v1/requests/?updated_since=<last run>` and `/api/v1/assets/?updated_since=<last run>` every few minutes.

## 4. Imports

Both commands validate the whole file first and save nothing if any row is wrong. Without `--commit` they only check.

### Staff: `import_users`

```bash
.venv/bin/python manage.py import_users staff.csv --commit
```

| Column | Required | Notes |
| --- | --- | --- |
| `email` | Yes | Matches existing accounts, ignoring case |
| `first_name`, `last_name` | Yes | |
| `title`, `staff_number`, `job_title`, `building`, `office`, `phone_extension` | No | |
| `department`, `campus` | No | Code or name; must exist already |
| `roles` | No | Semicolon list: `personal_advisor`, `ict_director`, `csm`, `technician`, `operations_manager`, `procurement_officer`, `auditor`, `system_admin` |

Existing accounts are updated. Roles are added; `--replace-roles` also removes roles missing from the file. New accounts get a random password nobody knows: staff then sign in through the directory, or choose a password with **Forgotten your password?**

### Asset register: `import_assets`

CUT ICT 40 §5.2 refers to an existing asset management system. Export its register to CSV and load it:

```bash
.venv/bin/python manage.py import_assets register.csv --commit
```

| Column | Required | Notes |
| --- | --- | --- |
| `asset_tag` | Yes | Existing CUT tags are kept |
| `category` | Yes | Code or name, for example `LAP` or `Laptop` |
| `make`, `model` | Yes | |
| `serial_number` | For categories that need one | Must be unique |
| `specification` | No | |
| `condition` | No | `new`, `good` (default), `fair`, `poor`, `faulty` |
| `status` | No | `in_stock` (default) or `allocated` |
| `holder_email` | For allocated assets | Import staff first |
| `campus`, `building`, `office` | No | |
| `supplier`, `purchase_order`, `acquired_on`, `warranty_expires_on`, `notes` | No | Dates as `YYYY-MM-DD` |

Each imported asset gets an "Imported from the previous register" movement, so its history starts on the day of import.

If CUT keeps the old asset management system after go-live, treat this system as the record of ICT custody and feed the other system from the asset API above, rather than keeping two registers by hand.

## 5. Excel exports

CUT ICT 38 §5.7 records requests in Excel for tracking. The system keeps that habit without retyping:

- Reports and All requests offer an Excel export of the request register, with the current filters applied: reference, procedure, items, requester, stage, who holds it, key dates, Procurement reference, working days open and an overdue flag.
- The asset register page exports the register with the current filters.

## 6. Email

Point the `EMAIL_*` settings at the CUT mail server. Messages are plain text with a link to the request. If the server is unreachable, messages wait in the outbox and the five-minute job retries them; in-app notifications carry on regardless.
