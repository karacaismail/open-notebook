# ADR-024: Reconcile large research results through bounded, durable batches

Status: Accepted

## Problem

Partitioning the research input does not bound its output. Hundreds of working
findings and source-passage receipts can exceed the reconciliation input budget
after every research part has completed. Repeating the research calls cannot
resolve this. Removing findings or raising an unmeasured model limit would put
evidence integrity at risk.

## Decision

Keep the existing single-request reconciliation when it fits or has already been
submitted. Otherwise partition complete working findings into measured batches,
with every referenced source receipt, unchanged part context, the complete shared
brief, all linked full prior-claim records and the global claim statement catalog.
Mark this scope explicitly and bind it to the complete prior register hash. Parent
reconciliations receive the complete prior register, including unlinked records;
the original register is never modified. Validate exact ordered finding equality,
part metadata, receipt equality and coverage before admitting any batch. Do not
split a finding, remove a field, or truncate a source passage. Each actual request
must fit the existing model policy; batch planning retains ten percent headroom.

Pin the payload, protocol and batch request hashes separately from the original
frozen research plan. Reuse the existing durable request journal and uncertain
submission rules. Never regenerate completed research parts to enable the new
reconciliation path. Provider receipts and source verification remain unchanged.

Reconcile batch reports through a bounded tree. Each parent receives the shared
brief, complete prior register, child narratives and a deterministic index of
finding identities, accepted statuses, prior-claim links and unresolved provenance.
Check the exact child finding lineage and part identities at every node. Reject
invented source URLs. Retain every intermediate report alongside the complete
original working evidence and deterministic claim ledger. Bound planning to 64
batches and four parent levels; stop without data removal if an indivisible item
does not fit or the tree cannot shrink.

## Limits

The final narrative sees child reconciliations, not every raw finding at once.
Its coverage list is lineage bookkeeping, not proof of direct original inspection
or semantic completeness. Cross-batch interactions can be missed or misinterpreted.
Prompts require unresolved dependencies and verification limits to remain explicit,
and a program-owned bilingual notice exposes this limitation even if the model
omits it. Unverified observations remain unverified in the retained working data
and claim ledger. Narrative correctness still requires substantive review.

This adds model calls and can increase latency. It does not relax context budgets,
alter source safety checks, claim lossless model summarization, or silently fall
back to a partial report when a batch or parent fails validation.

## Rich reconciliation annotations

Some providers return `reviewed_findings` as records with an explicit `id`,
justification and receipt annotations instead of plain identity strings. The
hierarchical runner may project those explicit identities through the unchanged
exact coverage check. It never infers missing identities from prose or fills them
from the input. Mixed forms, missing/duplicate/unknown identities and missing parts
still fail. The default merge parser remains strict.

The complete records and original response hash are appended as model annotations,
not source receipts or changes to the input's verification statuses. Unknown receipt
identities and conflicts with recorded verification are explicitly listed as
unresolved; they are never guessed, repaired or accepted as matching sources. A
program-owned bilingual notice states that these opinions cannot promote an
unverified finding. Narrative text and every annotation field remain unchanged,
and the original provider response stays immutable in the request journal. This
compatibility path requires no replacement model call or frozen prompt change.

## Restoring a documented omitted citation selector

A provider can omit the `fields` query from a supplied Semantic Scholar DOI
detail URL. The [official API tutorial](https://webflow.semanticscholar.org/product/api/tutorial)
documents this parameter as response field selection. For this exact HTTPS
endpoint only, a missing query can be restored from one unique full URL present
in the actual dispatched input, the allowed evidence and its source receipts. A
variant seen only by another batch is not a restoration candidate. The DOI, scheme, host and path
must be identical. Changed queries, fragments, unknown endpoints, identity or
authentication parameters and multiple possible variants still block. Other
unprovided URLs still fail the exact citation gate; no generic URL normalization
or new source admission is introduced.

Only the derived report's URL spans change. Raw provider responses, frozen
requests, findings and receipts remain immutable. A bilingual rendering notice
and byte-offset audit preserve response/report hashes, original URL hashes,
restored full URLs and receipt identities. This restores an existing citation;
it does not verify the source or promote a finding's status. Completed requests
are replayed locally without replacement model calls.

## Validation

Regression tests reject omitted or duplicate findings, altered source receipts,
changed statuses, lost negation/conditions, changed part dependencies and prior
registers, and invented citations. Tests cover within-part batching, indivisible
records, bounded depth/convergence, preserved intermediate outputs and idempotent
recovery after interruption. Live planning is validated offline against the saved
research results before deployment; no test calls the model provider.
