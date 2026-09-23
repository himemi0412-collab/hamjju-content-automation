# Automation reliability

## Actual execution path

GitHub `daily.yml` schedule/manual dispatch → checkout and cached budget/state restore → Python/system dependencies → `pytest -q` → OAuth materialization for media modes → `doctor` and `app.preflight` → `app.main` command → Notion fetch and eligibility → AI generation → independent QA → optional media generation and media verification → Notion append/attachments and optional private YouTube upload → Notion status update → readback → `validate_results` → `production-results.json` → status report/artifact/notification.

`dry_run` reads one eligible Notion page and does not run paid generation or change the page. The GitHub run conclusion alone does not prove a content item succeeded. `output_verified` means the page's saved status and produced blocks were read back; Shorts also check any claimed video attachment, media verification, and the saved YouTube URL. When automatic upload is configured, the authorized YouTube account must also return that exact video ID and privacy setting through `videos.list`.

## Recovery rules

1. Identify the first failing step, run ID, exact page and operation. Separate HTTP 429/5xx/transport failures from 401/403, invalid input, quota, and code errors. Do not log response bodies or signed media URLs.
2. `PRECHECK_FAILED` blocks production. A missing credential, ledger, resource, or write permission is a configuration error; 401/403 is an authentication error; 429 may represent a rate limit; 5xx and network failures may be transient. Only safe read calls receive bounded backoff.
3. Before retrying an item after an upload or partial Notion write, inspect the exact page, stored URL, output artifact, and previous run. Never assume another generation or upload is idempotent. Manual repair paths need exact page and artifact identity.
4. Add a failure reproducer, fix the cause, run related tests and the full suite, then run a read-only dry run. Inspect the workflow summary and independently read saved results after any authorized production run.
5. Mark an item complete only if QA passes, output readback passes, and the required media/upload evidence exists. Unknown evidence is incomplete. Do not promote private uploads to public without the configured approval.

## Known limits

- Local SQLite and budget ledgers are restored from GitHub caches. Cache retention or restore failure can interrupt production; the budget ledger fails closed when required. GitHub workflow concurrency serializes runs, but an API response lost after a remote write still needs exact-page reconciliation.
- The preflight checks credential presence, local paths and budget, and read-only Notion identity/source access. It does not determine remaining OpenAI/Fal/YouTube quota or confirm all provider credentials in advance; provider failures must still be diagnosed from the first failing operation.
- Media, Notion, and YouTube readback are checked in-process. A transient readback failure after an upload stops success reporting and leaves the uploaded page for exact-page reconciliation; it must not trigger another upload automatically.
- Secrets are referenced by workflow name, never read or reported in diagnostics. GitHub issue/email delivery is separate from successful content production.
