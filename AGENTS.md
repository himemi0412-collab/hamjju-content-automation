# Repository reliability rules

- Read the failing Actions step and the relevant code, configuration, and environment before changing anything. Distinguish external API failures from application failures.
- Make the smallest justified change and preserve working production behavior. Do not remove or disable tests, assertions, quality gates, or exceptions to obtain a green run.
- Never hardcode credentials or production IDs as a temporary fix. Never print secrets, tokens, signed URLs, or personal data to durable logs.
- Retry only bounded, safe, transient operations. Review side effects and idempotency before retrying writes, generation, or uploads.
- Add a regression test for each confirmed bug. Run the related tests and the full suite after changes; a failing test blocks a success report.
- Inspect the diff and changed file list. Check the actual workflow path, preflight, dry run, and independent result readback before calling production complete.
- Report only observed results. If a required check was not performed or evidence is missing, state that plainly and do not call the task successful.

See `docs/automation-reliability.md` for the workflow and recovery procedure.
