# ADR-025: Bound complete claim-register passes before synthesis

## Problem

A retained research review can contain hundreds of new findings and the complete
history of prior assessments. Splitting the report text is insufficient when the
full claim register is copied into every subsequent reduction request. In a real
GenUI run, that register alone exceeded both providers' measured input budgets.

## Decision

Keep the existing full-register path unchanged when it fits. Otherwise freeze a
`bounded-claim-register-v1` plan before any synthesis request is submitted:

- Preserve the complete register in the journal, and the complete original
  evidence in its existing byte-verified parts.
- Assign each complete claim record, including every condition, counter-source,
  assessment and limitation, to exactly one additional bounded `R` batch. Keep
  the shared user brief in each record batch. Never split an indivisible record
  or drop a record to make it fit.
- Give parent requests an explicitly scoped catalog containing every original
  statement, identity, source link and input assessment status when that fits.
  If the statements themselves exceed the shared parent budget, freeze a
  clearly labelled `claim-lineage-only` catalog: every identity, source link and
  input status remains; the complete original statements and histories are
  inspected in the mandatory record batches. This parent sees those batch
  reports and cannot claim direct access to all original statements. Identical source
  URLs use deterministic dictionary references with an exact URL mapping. The
  catalog is not the full assessment history and is never labelled lossless.
- Validate exact `reviewed_claim_ids` in record-batch responses and every parent
  that incorporates their lineage. Validate all source URLs against supplied
  evidence. Preserve immutable requests, completed responses and receipts.
- Recheck complete record reconstruction, catalog identity and all measured
  budgets on resume. Retain the existing 64-call and four-reduction-level limits.
  Unknown submission outcomes, indivisible oversized records, malformed output,
  invented sources and missing, repeated or changed identities still block.

Both providers use the same deterministic input record batches, measured against
every peer's admission policy. Each provider independently analyzes those batches.
The final narrative includes a program-generated scope notice. All full records
and intermediate reports remain accessible in the local frozen journals.

## Limits

A claim-ID list proves bookkeeping, not semantic understanding. A parent does not
read all raw assessment histories simultaneously. Conditions or cross-part
interactions can still be misunderstood; the prompt and final notice say so.
Source-passage matching does not establish truth. No input budget or calibration
margin is increased by this change. A catalog that cannot fit still blocks.

## Validation

Regression tests cover reconstruction, changed conditions, omitted or duplicated
records, reordered batches, altered shared context, changed hashes, invented or
missing response identities, measured limits, legacy plan compatibility, receipt
reuse and failure before resubmission. Live-data preflight is read-only and marked
as a projection until all upstream reports actually finish.

## Completed output continuation recovery

Claude synthesis now records the CLI event stream, including every assistant
artifact segment. The model, effort, tool prohibition and session-persistence
settings remain unchanged. Joining is permitted only across the already tested,
explicit synthetic output-limit continuation boundary, with one session,
successful termination and an exact terminal-tail match. Reasoning and tool
messages never enter the report. Original receipts and captures remain immutable.
The changed CLI flags change the runtime fingerprint; deployment must validate
and record the new runtime rather than bypassing its calibration gate.

For an older completed response containing invalid JSON syntax, the synthesis
runner first reads its durable receipt. A provenance-verified complete artifact
is retained separately and checked against the same coverage and source rules.
If no complete artifact was recorded, Claude may regenerate that intermediate
once using the exact frozen request and a new, explicitly journaled request ID.
The replacement consumes the existing 64-call allowance. Its failure, including
a confirmed provider rejection, cannot trigger another regeneration on resume.
An uncertain replacement is observed through its receipt, never submitted twice.

The journal and final report distinguish regeneration from reconstruction. Both
raw responses, their hashes and their common input hash are retained. Schema,
coverage, invented-source, duplicate-key, non-finite-number, receipt-integrity,
pause and budget failures cannot use this exception. JSON parsing now rejects
ambiguous duplicate keys and non-finite numbers. A syntactically valid artifact
does not establish semantic completeness or equivalence to the missing output.

## Frozen boundaries and resume cost

Existing partitions can end inside a source URL in a long serialized report.
A model accurately quoting that literal prefix is not inventing a new source.
Identify this case only when the exact prefix ends at a verified frozen part
boundary and the complete literal address exists at that same position in the
byte-verified original. Similar addresses and unrelated prefixes still fail.

Preserve the raw model response. In derived working prose replace only complete
occurrences of that prefix with a collision-free marker, and append a programmatic
notice identifying it as **not a source**. Display the prefix with an inert scheme;
show its complete original address as a provenance mapping, not as verification
of a claim. Never replace prefixes inside valid complete URLs. Reverse all markers
and require exact reconstruction before using this representation. Store the
boundary offsets, original part/source hashes, occurrence counts, response/report
hashes and notice in a separate journal record. The normal source allowlist is
unchanged; any remaining new address blocks. No original packet is repartitioned.

On resume, verify the saved plan's input hash, full ordered byte reconstruction,
claim-register integrity and pinned plan hash instead of recomputing every binary
partition search. Preserve its progress fields. Every actual subrequest still
passes the current measured input budget and runtime calibration checks. Other
policy blocks are never cleared by plan reuse.
