# Research availability and optional-provider development plan

Updated: 2026-09-24. Scope: continuing research safely when one parallel provider is unavailable.

## Delivered in the first increment

- Explicit per-stage Skip, with a distinct persisted state and user-action audit record.
- Two-step confirmation, risk acknowledgement and prevention of duplicate submissions.
- A minimum of one completed parallel report; no skipping a merge or the final decision.
- Confirmed local stop before skipping an active request; completed reports remain immutable.
- Frozen downstream inputs prevent retroactive changes to provider coverage.
- Later packets record the absent provider. Prompts prohibit invented contributions or consensus.
- Progress separates resolved stages from actual saved reports and labels skipped contributions.
- Quota errors no longer trigger the 1/5/30/90/250-minute retry sequence.
- Terminal Gemini errors are parsed separately from report content; known reset times persist in the account bridge and prevent premature requests.

## Planned next increments — not implemented

| Priority | Capability | Acceptance criteria |
| --- | --- | --- |
| P1 | Select required and optional providers when creating research | All remain enabled by default. The selected policy is stored with the run; optional never implies automatic omission without an explicitly accepted policy. |
| P1 | Provider availability card | Distinguish CLI/account quota, web Deep Research quota, sign-in, network failure and unknown availability. Display reset time in the user's timezone and label unknown times honestly. |
| P1 | Configurable evidence threshold | Minimum completed providers and mandatory roles are validated before allowing a skip. Warn that a single report provides no independent model cross-check. |
| P2 | Opt-in continuation after quota reset | Schedule at the reported reset, never before it. Recheck availability without resubmitting a completed report. Persist the user's choice through restarts. |
| P2 | Stop-and-skip as one guided action | Show the interruption risk, confirm cancellation, then revalidate the stage and dependencies before skipping. An unconfirmed cancellation must block the skip. |
| P2 | Fork after a skipped contribution | Create a separate research revision for later evidence, leaving the original run and frozen packets unchanged. No silent in-place restoration. |

## Validation gates

Test quota exhaustion with process exit code zero, report text containing misleading quota/auth keywords, restart persistence, expired reset times, unknown reset times, duplicate clicks, stale confirmation snapshots, running-stage races, missing peer evidence, immutable downstream packets and attempted imports into skipped stages. Browser checks must cover narrow screens, keyboard-accessible confirmation, visible missing-provider status and accurate report counts. Tests and previews must never launch a paid research request accidentally.
