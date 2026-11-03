# Core Banking System Notes

Small DB2/COBOL core banking estate used to demonstrate LegacyNexus AI.

## Batch schedule
- **NIGHTLY** runs PAYMENT (posts the daily payment file) and then REPORT12 (daily postings statement).
- **MONTHEND** calls the stored procedure SP_CLOSE_DORMANT to mark inactive accounts as dormant.

## Online access
- The Customer Lookup API (API_CUSTOMER) is served by the CUSTOMER program.

## Known history
- LEGACYTX was the old branch telex extract. It was superseded in 2009 and is no longer scheduled.
- AUDIT_ARCHIVE was filled by a retired archiving tool and has no current users.
