# Roles and permissions

Every active staff account can raise requests. Workflow roles sit on top of that and are granted per person in the administration site (Users, then the Roles table on the user's page). One person can hold several roles.

## Roles

| Role | Code | SOP basis | What the role does |
| --- | --- | --- | --- |
| Requester | none (every user) | ICT 38 §5.1, ICT 40 §5.1, ICT 42 §5.1 | Raises requests, answers returns, signs issue vouchers |
| Personal Advisor | `personal_advisor` | ICT 38 §5.1 | Checks equipment requests are complete and forwards them to the ICT Director |
| ICT Director | `ict_director` | ICT 38 §5.2, §5.6; ICT 42 §5.3 | Comments and forwards, approves purchases and replacements, declines, cancels after approval |
| Client Services Manager | `csm` | ICT 38 §5.3; ICT 42 §5.2 | Assigns technicians, recommends replacements, reassigns work |
| Help Desk Technician | `technician` | ICT 38 §5.4, §5.5, §5.8, §5.9; ICT 40 §5.2; ICT 42 §5.4 to §5.6 | Checks stock, writes specifications, deals with Procurement, logs deliveries, raises vouchers |
| Operations Manager | `operations_manager` | ICT 38 §5.6 | Cross-checks specifications before the Director's approval |
| Procurement Officer | `procurement_officer` | ICT 38 §5.8; ICT 42 §5.5 | Optional. Posts purchase order numbers, delivery dates and status on requests sent to Procurement |
| Quality Auditor | `auditor` | ISO 9001 and ISO 21001 audits | Reads everything, including the archive and reports. Cannot act |
| System Administrator | `system_admin` | | Reads all requests, reports, delegations and the archive. Cannot act on requests. Administration site access needs a superuser account (see below) |

## Who can see what

| Record | Requester | ICT staff roles | Procurement Officer | Others |
| --- | --- | --- | --- | --- |
| Own drafts | Yes | Only their own | Only their own | No |
| Submitted requests | Own only | All | Those sent to Procurement | No |
| Archive | Own archived requests by link | Director, CSM, auditor, administrator | No | No |
| Attachments | Own requests | Requests they can see | Requests they can see | No |
| Issue vouchers | Own | All | No | No |
| Asset register | Own assets | All | No | No |
| Reports and Excel | No | Director, CSM, Operations Manager, auditor, administrator | No | No |

"ICT staff roles" are Personal Advisor, ICT Director, CSM, Help Desk Technician, Operations Manager, Quality Auditor and System Administrator.

A user who opens a request they may not see gets the same "not on file" page as for a request that does not exist. The system never confirms that a reference exists to someone outside it.

## Who can act

The transition tables in [03-workflows.md](03-workflows.md) list the role for each step. On top of the role, four checks apply:

1. The request must be at the stage the step starts from.
2. The requester cannot act on any step except their own: submit, answer a return, cancel before approval, and sign or dispute the voucher.
3. Technician steps belong to the assigned technician. Allocation requests that nobody has taken yet are open to every technician except the requester.
4. The assigned technician cannot confirm the specifications or approve the purchase, whatever other roles they hold.

## Delegation

An officer who holds an approval role can hand it to a colleague for a fixed period: Personal Advisor, ICT Director, Client Services Manager or Operations Manager. Technician work moves by reassignment instead.

- The delegate sees the role's queue and acts with the role's powers until the end time.
- Every decision shows both names: "Dee Deputy, ICT Director, for Dr Rumbidzai Mutasa". The signing statement says "acting for".
- The holder or a system administrator can end a delegation early. A delegation also stops working the moment the holder loses the role or the account is deactivated.
- A delegate cannot act on requests they raised.

When the only holder of a role raises a request, nobody can act at that stage. The request page explains this and asks for a delegation.

## The administration site

The Django administration site at `/admin/` holds master data (campuses, departments, asset categories, holidays), user accounts and roles, delegations, API keys and the email outbox. Only superuser accounts can open it. Workflow roles never grant access, so an ICT Director cannot edit records behind the workflow.

Requests, vouchers, audit entries and asset movements are read-only in the administration site. Asset tags, status and holder are read-only too. Those change only through workflow steps and the asset page, which write the movement log.
