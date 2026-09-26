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
