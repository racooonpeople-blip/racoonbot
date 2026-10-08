# RacooonBot security

## Changes in this branch
- Reserve free monthly file slots in a PostgreSQL transaction **before** paid API requests. Stop safely if the database is down. Release a slot if transcription fails.
- Limit costly AI requests per chat (12 / 10 minutes) and across the running process (80 / minute). These are in-memory limits that reset on restart. Configure provider-side API spend limits separately.
- Check declared media size before downloading.
- Do not process group or channel messages: a shared chat must not be able to access another user's transcript archive.
- Delete old usage accounting events automatically.
- **Optional** transcript retention: `TRANSCRIPT_RETENTION_DAYS` is 0 (no deletion) by default. Set a positive integer only after deciding and communicating a retention period; deletion is irreversible. When enabled, pruning runs at startup and at most once per day after saves.

## Manual owner tasks
1. GitHub Settings → Emails → keep your address private, and set the GitHub-provided noreply email for *future* commits. Prior 46 commits still contain the old author email; this branch cannot rewrite published commit history.
2. Configure hard OpenAI API budget/usage alerts, and review Railway's private environment variables; never publish credentials.
3. Determine whether public visibility is appropriate for this bot. Making the repository private and rewriting history are separate account-level actions.
4. Choose and disclose a transcript retention period before enabling deletion.
5. Test the change on a staging Telegram bot with a test database before deploying into production. This is a code audit, not a full penetration test.
