# Module migration validation

Validated on 2026-09-21 with the original repository at `3127f14`.

| Check | Result |
|---|---|
| Module registry/proxy/auth/persistence/disable leases | 17 tests passed |
| Research sidecar, including real local DOM fixtures and portable extension installer | 102 tests passed |
| Account adapter | 15 tests passed |
| Frontend with selected modules | 181 tests passed |
| Core-only frontend | 164 tests passed |
| TypeScript, both frontend configurations | Passed |
| ESLint, new module UI and changed navigation | No errors |
| Linux production frontend build, selected modules | Passed |
| Linux production frontend build, core only | Passed |
| Browser UI, synthetic failure fixture | Desktop/mobile, Markdown dialog, focus return, timer, all failure reasons; no model requests |
| Browser UI, live local deployment | Research feedback and module settings rendered without page errors; read-only inspection |
| Data preservation across application update | All 15 existing completed report fingerprints unchanged |

The research/account/audio services and the source worker were left running in
place. Only the notebook API and frontend processes were restarted. No data mounts
or database schemas were changed. A previous-image tag and an application-code
backup were retained privately by the operator.

These checks do not claim the browser providers will always accept future jobs,
that model agreement proves accuracy, or that the final research packet fits every
model's context limit. No billable eight-stage research was started for this module
migration. Runtime toggling gates application access; it does not uninstall native
services, remove reports or undo compiled build customizations.
