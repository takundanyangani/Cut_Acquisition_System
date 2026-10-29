# Observations on the SOPs

Building the system meant reading CUT ICT 38, 40 and 42 (Revision 1, effective 22 January 2026) step by step. These notes record where the documents left a decision open, where they disagree with each other, and small editorial points. They are offered to the Quality Management System team for Revision 2. Each note says how the system handles the point today.

## Gaps in the process

| # | Procedure | Observation | How the system handles it | Suggested SOP change |
| --- | --- | --- | --- | --- |
| 1 | All three | No step covers a request that is incomplete, refused or no longer needed | Return for changes at every review stage; decline by the ICT Director; cancellation by the requester before approval and by the Director after | Add return, decline and cancel steps with mandatory reasons |
| 2 | All three | Nothing stops an officer approving their own request | Nobody acts on a request they raised; independent checks for the assigned technician | State separation of duties |
| 3 | All three | No provision for an officer on leave | Dated delegations; both names on the signature | Name who may act for each role |
| 4 | CUT ICT 38 §5.1, CUT ICT 40 §5.1 | Both start with "the user initiates a request". It is not clear which applies when the user does not know whether ICT has stock | One request centre. An allocation request with no stock converts into an equipment request | Make CUT ICT 40 the first step and CUT ICT 38 the route when stock is not available |
| 5 | CUT ICT 38 §5.4 | The technician finds specifications without first checking stock | The technician may issue from stock at that step | Add "checks stock first" |
| 6 | CUT ICT 38 §5.7 | Recording in Excel duplicates the system register | The register is the record; Excel export keeps the habit | Replace with "the request is recorded in the ICT acquisition system" |
| 7 | CUT ICT 38 §5.9 | "Makes follow-ups" has no interval | Follow-up dates and reminders, default every 5 working days | Set an interval |
| 8 | CUT ICT 38 KPI 6.1 | "Expeditiously" has no target, so it cannot be measured | Working-day target per stage, proposed in [03-workflows.md](03-workflows.md) | Adopt numeric targets |
| 9 | CUT ICT 40 §5.2 | No named approver for issuing stock | The technician issues; the voucher and the user's signature control the hand-over | Confirm the technician's authority, or add CSM approval for high-value items |
| 10 | CUT ICT 40 §5.3 | No step when the serial, specification or condition does not match | The user can dispute; assets return to stock; a corrected voucher follows | Add a dispute step |
| 11 | CUT ICT 42 | Silent on the machine being replaced | The voucher records whether the old machine came back and its condition | Add a return step for the old asset |
| 12 | CUT ICT 42 §5.4 | The specification sheet has no cross-check, unlike CUT ICT 38 §5.6 | Follows the SOP: no Operations Manager step on replacements | Decide whether the Operations Manager should check replacement specifications too |
| 13 | CUT ICT 42 §5.2 | The CSM "recommends". It is not said what happens if the CSM does not recommend | Either way the request goes to the Director with the CSM's position recorded | State that the Director decides in both cases |
| 14 | CUT ICT 42 §5.5 | Partial deliveries are not mentioned | Deliveries can be logged in parts | Mention partial deliveries |
| 15 | CUT ICT 38, 40 | Requests for several items may be issued in part | A short voucher needs a comment that explains the difference | State how partial issue is recorded |

## Records and retention

| # | Observation | How the system handles it |
| --- | --- | --- |
| 16 | CUT ICT 38 keeps "Asset Equipment request" for 1 semester; CUT ICT 42 keeps "Equipment Requests" for 1 year. The records are similar but the periods differ | Each request type follows its own SOP |
| 17 | "1 semester" has no length in days | Taken as 183 days; set by `ACQ_RETENTION_EQR_DAYS` |
| 18 | An equipment request archives after a semester, but its issue voucher must stay at the Helpdesk for a year (CUT ICT 40, 8.1) | Vouchers keep their own one-year period |
| 19 | "Disposition: Archives" does not say whether records are ever destroyed | Nothing is deleted |
| 20 | Records "kept in the CSM's office" and "at the Helpdesk" assume paper | Electronic records with a printable record and voucher |

## Editorial points

| # | Procedure | Observation |
| --- | --- | --- |
| 21 | CUT ICT 38 §5.1 | "Personal Advisor" may mean the Director's Personal Assistant. The system uses the SOP's term |
| 22 | CUT ICT 40 §3 | Names the "Executive ICT Director"; the other procedures say "ICT Director" |
| 23 | CUT ICT 40, 42 | Section numbering jumps from 5 to 7. CUT ICT 42 has "8.0 Retained Documented Information" and then "8. References" |
| 24 | CUT ICT 42 | The title cell was not legible in the copy used. The system calls it "Asset Acquisition (replacement)" from its purpose statement. Confirm the official title |
| 25 | CUT ICT 42 §5.2, §5.3 | "the replacement of ICT equipment replacement" repeats a word |
| 26 | CUT ICT 38 §5.3 | "The Client service manager forwarded the request is to" reads as a typing slip |
| 27 | CUT ICT 42 KPI 7.1 | "Movement of ICT assets ... is approved by ICT personnel" describes a control, not a measure. The system reports the share of movements recorded by a named ICT officer |
