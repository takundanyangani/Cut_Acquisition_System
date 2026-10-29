# Administration

System administrators work in two places: the administration site at `/admin/` (superuser accounts only) and the command line on the server.

## Accounts

To add a person, open Users and choose **Add user**. Enter the email, names and a temporary password. The person must choose a new password at first sign-in. Then open the user again to fill in the title, staff number, department, campus and office, and add roles in the Roles table at the bottom.

For many people at once, use `import_users` (see [07-integration.md](07-integration.md)).

To grant or remove a role, open the user, add or delete a row in the Roles table, and save. The change takes effect on the person's next page load. The "Granted by" column records who added each role.

When someone leaves, untick **Active** and save. Do not delete the account: their name must stay on the requests and vouchers they signed. Then:

1. Check **All requests** for anything waiting on them. The request page warns when nobody can act.
2. Ask the CSM to reassign their technician work.
3. If they held an approval role alone, grant the role to their successor or set up a delegation.
4. Check the asset register for equipment they hold and record its return.

A lockout clears itself after 15 minutes. Sign-in history is under Login attempts.

## Delegations

Role holders set up their own delegations under Delegations in the main menu. Administrators can see and end every delegation there, or create one for an absent officer in the administration site. The delegator must hold the role directly.

## Master data

| Item | Where | Notes |
| --- | --- | --- |
| Campuses | Organisation and system, Campuses | Untick Active to hide one from new forms; old records keep it |
| Departments | Organisation and system, Departments | Same |
| Holidays | Organisation and system, Holidays | Add each public holiday. Stage targets and reminders skip these dates |
| Asset categories | ICT asset register, Asset categories | "Requires serial" decides whether an asset of this type needs a serial number |

## Requests and the archive

Requests are read-only in the administration site. Stages change only through the workflow, which signs and logs every step. To correct a mistake, act on the request through the normal pages: return it, decline it, or cancel it with a reason.

`apply_retention` moves closed requests to the archive after the retention period of their procedure. Archive, in the main menu, shows them to auditors, the ICT Director, the CSM and administrators.

## Checking the audit trail

```bash
.venv/bin/python manage.py verify_audit_chain
```

```bash
.venv/bin/python manage.py verify_audit_chain EQR-2026-0042 RPL-2026-0007
```

The command prints `All N audit trail(s) are intact.` or lists each broken entry and exits with status 1. Cron runs it every Sunday. A failure means someone changed the database directly; restore from backup and investigate.

## Email outbox

The Outbound emails page lists every message with its status. A failed message shows the SMTP error. Select messages and choose **Retry sending the selected messages** after fixing the mail settings. In-app notifications do not depend on email.

## API keys

Create keys with `manage.py create_api_key "<system name>"`. Revoke them under API keys with the Revoke action. "Last used" shows whether a key is still in use.

## Demo data

`manage.py seed_demo` loads fictional campuses, departments, holidays, one account per role and requests at several stages. It refuses to run when `DJANGO_DEBUG` is off and refuses a second run. Use it on training and test servers only. All demo accounts end in `@demo.cut.local` and share the password the command prints.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| "This request cannot move" on a request page | The role for that stage is vacant or held only by the requester. Grant the role or set up a delegation |
| No emails arrive | Outbound emails for errors; `sendtestemail`; the `deliver_emails` job in `jobs.log` |
| Reminders not sent | `send_reminders` runs on working days only; check the Holiday table and `jobs.log` |
| "Someone else updated this request" | A colleague acted first. The officer should read the latest version and act again if still needed |
| A user cannot see a request | They see only their own requests unless they hold an ICT role |
| Styles missing after an upgrade | Run `collectstatic` and restart Gunicorn |
| Server error page | `journalctl -u cutacq`; errors also email `DJANGO_ADMINS` |
