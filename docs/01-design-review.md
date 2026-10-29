# Plan, loophole review and revised plan

This document records how the system design was reached. It starts with the first plan, lists the holes found in it, and ends with the plan that was built. Each fix points to the module that implements it.

Source material: three CUT ICT procedures, all at Revision 1 and effective 22 January 2026.

| Procedure | Title | What it covers |
| --- | --- | --- |
| CUT ICT 38 | Equipment Request | A requirement that goes through the Personal Advisor to the ICT Director, then the Client Services Manager (CSM), a Help Desk Technician, the Operations Manager, the Director again, and Procurement |
| CUT ICT 40 | ICT Asset Allocation | Issuing a gadget that ICT already holds, on an issue voucher the user signs |
| CUT ICT 42 | Asset Acquisition (replacement) | Replacing a machine: CSM recommends, Director approves, procurement with a specification sheet, technician logs the delivered equipment |

## 1. First plan

1. Build three online forms, one per procedure.
2. Each form passes through a fixed list of approvers. Each approver sees an "Approve" button.
3. Send an email to the next approver after each click.
4. Keep an asset list that technicians update by hand.
5. Staff sign in with a username and password.
6. Export requests to Excel, since CUT ICT 38 step 5.7 records requests in Excel.

## 2. Loopholes found in the first plan

Each row names the hole, how it would hurt CUT, and the fix in the revised plan.

| # | Hole in plan 1 | Consequence | Fix |
| --- | --- | --- | --- |
| 1 | Three separate forms | A user who needs a laptop cannot tell whether to use ICT 38 or ICT 40. ICT 40 checks stock first; ICT 38 assumes a purchase. | One request centre with three request types and plain descriptions. An allocation request that finds no stock converts into an ICT 38 equipment request, linked both ways. |
| 2 | Approve is the only button | The SOPs describe the happy path only. Real requests get queried, sent back and turned down. | Every review stage has Return for changes (reason required). Only the ICT Director can decline. A returned request goes back to the stage that returned it, so the reviewer who asked the question sees the answer. |
| 3 | No separation of duties | The ICT Director could approve their own laptop. A technician could issue stock to themselves. | Nobody can act on a stage of a request they raised. The rule covers delegates too. |
| 4 | One person per stage | When the Director is away, every request stops. That recreates the walking-around problem. | Dated delegations. The acting officer signs "for the ICT Director" and the stamp shows both names. |
| 5 | A click is not a signature | Anyone at an unlocked computer could approve. Nothing proves who signed. | Signing stages ask for the user's password again. Sessions end after 30 idle minutes. Each action enters a hash-chained audit log that a command can verify. |
| 6 | Admin can edit anything | A database admin could change a request's stage or an asset's holder with no trace. | The Django admin shows requests, vouchers and audit entries read-only. Asset holder and status change only through workflow actions that log a movement. |
| 7 | Concurrent clicks | Two CSMs could assign the same request. Two technicians could issue the same laptop. | Row locks plus a version number on every request. A stale form gets a clear message. Assets are locked and checked for "In stock" before they join a voucher. |
| 8 | Replacement leaves the old machine out | ICT 42 replaces a machine but never says what happens to the old one. The register drifts from reality. | The issue voucher for a replacement records whether the old asset came back and its condition. Uncollected assets stay on the user's record. |
| 9 | Voucher reads live asset data | If someone later edits an asset's specification, the signed voucher appears to say something else. | Voucher lines store a snapshot of tag, serial, specification and condition at the time of issue. |
| 10 | Excel copy of every request | Double entry. Two sources of truth. | The system register is the record. Excel export keeps the ICT 38 step 5.7 habit working without retyping. |
| 11 | Follow-ups forgotten | ICT 38 step 5.9 says "the technician makes follow-ups" but sets no dates. | Each procurement follow-up sets the next follow-up date. A daily job reminds the technician. |
| 12 | KPIs cannot be measured | "Expeditiously responded" (ICT 38 KPI 6.1) has no number. | Each stage has a target in working days (configurable). Reports show time per stage, overdue items, voucher acknowledgement and asset movements. |
| 13 | Records deleted or kept forever | The SOPs set retention: 1 semester for ICT 38 records, 1 year for ICT 40 vouchers and ICT 42 requests, then "Archives". | A retention job moves closed records to the archive after the period for their type. Nothing is deleted. Auditors can still read archived records. |
| 14 | Uploads served from a public folder | Specification sheets and delivery notes would be reachable by anyone with the link. A renamed executable could be uploaded. | Files are checked by extension, size and file signature, stored under random names, and served only through a view that checks the reader may see the request. |
| 15 | No login protection | Password guessing. | Argon2 password hashing, lockout after 5 failures in 15 minutes per account (and 20 per IP address), forced password change for accounts created by an administrator. Failed signing attempts count toward the lockout. |
| 16 | Email is the only channel | If the CUT mail server is down, approvers never hear about a request. | In-app notifications plus an email outbox that retries failed messages. |
| 17 | Reference numbers from drafts | Abandoned drafts leave gaps in the numbering, which auditors query. | Drafts have no reference. The number (for example EQR-2026-0042) is issued on first submission, from a locked counter per type and year. |
| 18 | Deliveries assumed complete | Suppliers often deliver part of an order. | Deliveries can be logged in parts. The request moves to issuing when the technician marks the delivery complete. |
| 19 | No route for a wrong item | ICT 40 step 5.3 has the user sign for serial, specification and condition, but says nothing if they don't match. | The user can dispute the voucher with a reason. The assets go back to stock and the technician raises a corrected voucher. |
| 20 | Procurement outside the system | The Procurement Department may not use the system at first. | The technician records the procurement reference. An optional Procurement Officer role can post updates (PO number, expected delivery) when Procurement joins. |
| 21 | Technician leaves or is off sick | Assigned work sits unseen. | The CSM can reassign at any fulfilment stage. Allocation requests sit in a shared Helpdesk queue until a technician claims one. |
| 22 | Edits after approval | A requester could change quantities after the Director signed. | Requesters edit only in Draft or Returned. Each edit after return is logged. |
| 23 | Stand-alone system | CUT already runs an asset management system (ICT 40 step 5.2) and a staff directory. | CSV import for existing assets and staff, a read-only JSON API with revocable keys, optional LDAP sign-in, and support for running under a sub-path of the CUT portal. |
| 24 | Silent blockage | If the only Director raises a request, no one is allowed to approve it. | The request page warns that no eligible approver exists and tells the user to ask for a delegation. |

## 3. Revised plan (built)

### 3.1 Request types

| Type | Prefix | Procedure | Route |
| --- | --- | --- | --- |
| Allocation from ICT stock | ALC | CUT ICT 40 | Helpdesk technician, issue voucher, user signs. No stock: converts to an equipment request. |
| Equipment request | EQR | CUT ICT 38 | Personal Advisor, ICT Director review, CSM, technician specification, Operations Manager, ICT Director approval, Procurement, delivery, issue voucher, user signs |
| Replacement request | RPL | CUT ICT 42 | CSM recommendation, ICT Director approval, technician specification sheet, Procurement, delivery, issue voucher, user signs |

ICT 40 governs every hand-over, whatever route brought the asset in. A technician who finds stock at the specification stage can issue it instead of buying.

### 3.2 Roles

Requester (every active user), Personal Advisor, ICT Director, Client Services Manager, Help Desk Technician, Operations Manager, Procurement Officer, Quality Auditor (read only), System Administrator. One person can hold several roles.

### 3.3 Controls

- Separation of duties on every non-requester action.
- Password re-entry on signing actions.
- Hash-chained audit trail per request, checked by `manage.py verify_audit_chain`.
- Row locking and version checks on every transition.
- Working-day targets per stage, with reminders.
- Retention and archiving per procedure.

### 3.4 Technology

- Django 5.2 LTS (supported to April 2028) on Python 3.12 or later.
- PostgreSQL in production. SQLite for a developer laptop.
- Server-rendered pages with hand-written CSS and a small amount of plain JavaScript. No Node build step, so the ICT team maintains it with Python skills alone.
- Fonts and icons served from the application. Pages work on the campus network without reaching the internet.
- Gunicorn behind Nginx, or Docker Compose.

### 3.5 Second review of the revised plan

The revised plan was checked again for new holes.

- Password re-entry fails for LDAP users if the check uses the local password hash. The signing check calls Django's `authenticate()`, which goes through whichever backend signed the user in.
- A strict Content Security Policy breaks inline styles used for chart bars. Charts use SVG attributes, and the CSP header relaxes only for the Django admin.
- A per-process cache would make lockout useless under several Gunicorn workers. Failed attempts are stored in the database.
- Sending email inside the request would slow approvals when SMTP is slow. Messages go to the outbox and send after the database commit.
- Returning a 403 for someone else's request tells an outsider the reference exists. The system returns 404 instead.
