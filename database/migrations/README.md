# Historical prototype SQL — NOT a migration chain

The four `20260921_*.sql` files are the early prototype scripts that the numbered chain in
`database/schema/` (001–033) superseded. `database/schema/013_schema_consolidation.sql` documents them and
`tests/test_schema_chain.py` keeps them as read-only history.

Do **not** apply them to any database. Schema changes are made only through the next forward migration in
`database/schema/` (next: 034), with its rollback, `SHA256SUMS` entry and rehearsal.
