"""Account re-research: frozen evidence -> fresh research -> passage checks -> reconciliation."""
import asyncio
import json
import uuid

from context_compaction import expand_prompt
from context_preparation import PreparationError, digest, envelope, render_segment, validate_plan
from packet_markdown import evidence_body
from review_contract import (MAP_INSTRUCTIONS, MERGE_INSTRUCTIONS, REVIEW_VERSION, blocks,
                             parse_map, parse_merge, partition, shared_brief, validate_repair, SchemaRepairNeeded)
from review_sources import SourceReader
from workflow import citations, report_packet

SOURCE_BATCH_SIZE = 24
VERIFICATION_POLICY = 'source-assessment-v1'
VERIFICATION_INSTRUCTIONS = '''\nSource assessment policy v1:
Only passage_matched_not_fact_checked receipts establish a quotation's textual presence.
passage_not_matched, insufficient_passage and source_unavailable are unresolved evidence, never proof of support or rejection.
A finding or prior assessment downgraded to unverified must remain unverified in the report.
model_status preserves the provider's original opinion; it is not the accepted verification status.
Keep these limitations explicit. Do not infer that an unavailable or unmatched source proves a claim false.
An original_anchor with scope claim_catalog comes from supplied shared context, not the current evidence part.
It must remain unverified and cannot establish that part's evidence coverage. Retain its explicit scope and original model_status.
An inline_identifier_markup anchor preserves the exact source text and byte offsets; no source words were changed.
'''


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def map_request(plan, part):
    index = plan['parts'].index(part)
    neighbors = {}
    for adjacent, edge in ((index-1, -1), (index+1, 0)):
        if 0 <= adjacent < len(plan['parts']):
            candidate = plan['parts'][adjacent]
            unit = blocks(candidate['text'])[edge]
            if len(unit) <= 4000: neighbors[candidate['id']] = unit
    return (MAP_INSTRUCTIONS + envelope(encoded({'brief': plan['brief'],
            'prior_claims': plan['claim_catalog'], 'part_id': part['id'],
            'neighboring_context_not_additional_evidence': neighbors,
            'part_count': len(plan['parts'])})) + render_segment(part, plan))


def plan_for(engine, run, stage, prompt):
    body, _ = evidence_body(expand_prompt(prompt))
    peers = [s for s in run['stages'] if s.get('account_profile') == 'research_review'
             and s['round'] == stage['round']] or [stage]
    packet = report_packet(run, stage)
    register = packet.get('evidence_register', {})
    catalog = [{'id': c['id'], 'statement': c['statement']} for c in register.get('claims', [])]
    fixed = MAP_INSTRUCTIONS + envelope(encoded({'brief': shared_brief(run), 'prior_claims': catalog}))
    # Leave capacity for live tool results, neighbor context and the structured response.
    plan = partition(body, lambda value: all(engine.measure_input(fixed+value, s)['utilization'] <= .35 for s in peers),
        indivisible_fits=lambda value: all(engine.measure_input(fixed+value, s)['utilization'] <= .5 for s in peers))
    plan.update(brief=shared_brief(run), claim_catalog=catalog, protected_register=register,
                contract_sha256=digest(MAP_INSTRUCTIONS+MERGE_INSTRUCTIONS))
    for part in plan['parts']:
        if not all(engine.measure_input(map_request(plan, part), s)['utilization'] <= .55 for s in peers):
            raise PreparationError('A re-research part and its shared context exceed the reserved budget.')
    for peer in peers:
        merged = dict(peer, account_profile='review_merge')
        if engine.measure_input(MERGE_INSTRUCTIONS+envelope(encoded(register)), merged)['utilization'] >= .7:
            raise PreparationError('The unchanged claim register leaves insufficient reconciliation capacity.')
    return plan


def summary(plan):
    return {'version': REVIEW_VERSION, 'plan_sha256': digest(encoded(plan)),
        'source_sha256': plan['source_sha256'], 'source_bytes': plan['source_bytes'],
        'parts': len(plan['parts']), 'minimum_calls': len(plan['parts'])+1,
        'coverage_verified': True, 'semantic_lossless': False, 'status': 'planned'}


def ledger(plan, nodes, stage_id):
    """Keep every prior identity and every new finding; never rewrite original claims."""
    findings = [f for node in nodes for f in node['findings']]
    claims = []
    for prior in plan['protected_register'].get('claims', []):
        assessments = [(f,a) for f in findings for a in f['prior_assessments'] if a['id']==prior['id']]
        statuses = {a['status'] for _,a in assessments}
        status = ('disputed' if 'disputed' in statuses or {'supported', 'rejected'} <= statuses else
                  'unverified' if not statuses or 'unverified' in statuses else next(iter(statuses)))
        claims.append({'id': prior['id'], 'statement': prior['statement'],
            'status': status,
            'sources': sorted({s['url'] for f,_ in assessments for s in f['sources']}),
            'counter_sources': sorted({s['url'] for f,a in assessments if a['status'] in ('disputed','rejected') for s in f['sources']}),
            'reason': ' | '.join(('Source verification incomplete. ' if 'model_status' in a else '') + a['reason'] for _,a in assessments) or 'Not reassessed in the working findings; original claim retained.',
            'counter_evidence': ' | '.join(f['counter_evidence'] for f,_ in assessments),
            'limits': ' | '.join(f['conditions']+' '+f['limits'] for f,_ in assessments)})
    for index,finding in enumerate(findings,1):
        claims.append({'id': stage_id+':C'+str(index).zfill(4), 'working_finding_id':finding['id'], 'statement': finding['statement'],
            'status': finding['status'], 'sources': [s['url'] for s in finding['sources']],
            'counter_sources': [s['url'] for s in finding['sources']] if finding['status'] in ('disputed','rejected') else [],
            'reason': ('Original passage belongs to shared claim context, not this evidence part; assessment remains unverified.'
                       if finding.get('original_anchor', {}).get('scope') == 'claim_catalog' else
                       'Source verification incomplete; original model assessment retained separately.'
                       if finding.get('verification', {}).get('unverified_sources') else
                       'Source passage matched; interpretation remains a model assessment.'),
            'counter_evidence': finding['counter_evidence'], 'limits': finding['conditions']+' '+finding['limits']})
    return {'claims': claims, 'blind_spots': [s for n in nodes for s in n['blind_spots']+n['dependencies']]}


class ReviewRunner:
    def __init__(self, engine, run, stage, prompt):
        self.engine, self.run, self.stage, self.prompt = engine, run, stage, prompt
        self.path = engine.input_path(run, stage).parent/'segmented-journal.json'
        self.reader = SourceReader(self.path.parent/'source-snapshots')
        self.usages = []

    async def persist(self):
        def write():
            temporary = self.path.with_suffix('.tmp'); temporary.touch(mode=0o600)
            temporary.write_text(encoded(self.state)); temporary.replace(self.path)
        await asyncio.to_thread(write)

    async def progress(self, status, current=None, request_id=None, **details):
        async with self.engine.lock:
            latest = await self.engine.get(self.run['id']); stage = self.engine.stage(latest, self.stage['id'])
            if latest.get('paused') or latest.get('control_state') or stage.get('control_state'):
                from engine import ServiceError
                raise ServiceError('Re-research paused; completed parts are saved.', 409, kind='interrupted')
            stage['preparation'] = dict(summary(self.plan), status=status, current=current,
                completed_calls=sum(j['status']=='completed' for j in self.state['jobs'].values()), **details)
            if request_id: stage['request_id'] = request_id
            await self.engine.store.save(latest)

    async def call(self, key, request, profile):
        from engine import ServiceError
        child = dict(self.stage, account_profile=profile)
        budget = await asyncio.to_thread(self.engine.measure_input, request, child)
        if not budget['fits']:
            raise ServiceError('Reconciliation exceeds its measured budget; all completed parts remain saved. No findings were removed.', 409, kind='context_limit')
        job = self.state['jobs'].get(key)
        if job:
            if job['input_sha256'] != digest(request): raise PreparationError('Saved re-research subrequest changed.')
            if job['status'] == 'completed':
                if digest(job['response']) != job['response_sha256']: raise PreparationError('Saved re-research response changed.')
                self.usages.append(job['usage']); return job['response'], job['usage']
            if job['status'] == 'in_flight':
                if not hasattr(self.engine.provider, 'recover'):
                    raise ServiceError('A previous re-research request has an uncertain outcome; it was not repeated.', 409, kind='submission_uncertain')
                child.update(request_id=job['request_id'])
                await self.progress('researching' if profile=='research_review' else 'reconciling', key, child['request_id'])
                try:
                    recover = getattr(self.engine.provider, 'recover_pending', self.engine.provider.recover)
                    response, usage = await recover(child, request)
                except ServiceError as exc:
                    if exc.settled:
                        job.update(status='rejected', error=str(exc), error_kind=exc.kind, request_settled=True)
                        await self.persist()
                    raise
                job.update(status='completed', response=response, response_sha256=digest(response), usage=usage)
                await self.persist(); self.usages.append(usage)
                return response, usage
        child.update(request_id=uuid.uuid4().hex, input_budget=budget)
        await self.progress('researching' if profile=='research_review' else 'reconciling', key, child['request_id'])
        if job:
            self.state.setdefault('attempt_history', []).append(dict(job, key=key))
        self.state['jobs'][key] = {'status': 'in_flight', 'request_id': child['request_id'],
            'input_sha256': digest(request), 'input': request, 'profile': profile, 'budget': budget}
        await self.persist()
        try:
            response, usage = await self.engine.provider.synthesize(child, request)
        except ServiceError as exc:
            if exc.settled or exc.kind in ('quota_wait', 'login_required', 'research_unavailable', 'calibration_required'):
                self.state['jobs'][key].update(status='rejected', error=str(exc), error_kind=exc.kind, request_settled=exc.settled)
                await self.persist(); raise
            raise ServiceError('Re-research request outcome is uncertain; automatic repetition is blocked.', 409, kind='submission_uncertain') from exc
        except Exception as exc:
            raise ServiceError('Re-research connection ended before a confirmed result; automatic repetition is blocked.', 409, kind='submission_uncertain') from exc
        self.state['jobs'][key].update(status='completed', response=response, response_sha256=digest(response), usage=usage)
        await self.persist(); self.usages.append(usage)
        return response, usage

    async def verify_source(self, source):
        # Do not release the stage lock while a cancelled reader still writes its cache.
        task=asyncio.create_task(asyncio.to_thread(self.reader.assess,source))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:await task
            except Exception:pass
            raise

    async def check_sources(self, node, part_id, receipts):
        """Checkpoint bounded batches, retaining all source pairs and negative checks."""
        if self.state.setdefault('source_verification_policy', VERIFICATION_POLICY) != VERIFICATION_POLICY:
            raise PreparationError('The saved source verification policy changed.')
        checks = self.state.setdefault('source_checks', {})
        pending = {}
        for finding in node['findings']:
            for source in finding['sources']:
                pair = {k: source[k] for k in ('url', 'quote')}
                pending[digest(encoded(pair))] = pair
        items = list(pending.items())
        checked = unverified = 0
        for start in range(0, len(items), SOURCE_BATCH_SIZE):
            for key, pair in items[start:start + SOURCE_BATCH_SIZE]:
                await self.progress('checking_sources', part_id, checked_sources=checked,
                    total_sources=len(items), unverified_sources=unverified,
                    source_batch=start // SOURCE_BATCH_SIZE + 1)
                if key in checks:
                    saved = checks[key]
                    if saved['source'] != pair or digest(encoded(saved['receipt'])) != saved['sha256']:
                        raise PreparationError('Saved source receipt changed; verification stopped.')
                    receipt = saved['receipt']
                    await asyncio.to_thread(self.reader.validate_receipt, pair, receipt)
                else:
                    receipt = await self.verify_source(pair)
                    if receipt.get('verification') not in ('passage_matched_not_fact_checked', 'passage_not_matched', 'insufficient_passage', 'source_unavailable'):
                        raise PreparationError('Source verification returned an unknown receipt status.')
                    checks[key] = {'source': pair, 'receipt': receipt, 'sha256': digest(encoded(receipt))}
                    await self.persist()
                receipts[key] = receipt
                checked += 1
                unverified += receipt['verification'] != 'passage_matched_not_fact_checked'
        for finding in node['findings']:
            unresolved = []
            for source in finding['sources']:
                key = digest(encoded({k: source[k] for k in ('url', 'quote')}))
                source['receipt_id'] = key
                if receipts[key]['verification'] != 'passage_matched_not_fact_checked':
                    unresolved.append(key)
            finding['verification'] = {'unverified_sources': len(unresolved), 'unverified_receipts': unresolved}
            if unresolved:
                finding.setdefault('model_status', finding['status']); finding['status'] = 'unverified'
                for assessment in finding['prior_assessments']:
                    assessment.setdefault('model_status', assessment['status']); assessment['status'] = 'unverified'
        await self.progress('checking_sources', part_id, checked_sources=checked,
            total_sources=len(items), unverified_sources=unverified)

    async def execute(self):
        from engine import ServiceError
        try:
            body, _ = evidence_body(expand_prompt(self.prompt))
            if self.path.exists():
                self.state = json.loads(await asyncio.to_thread(self.path.read_text))
                if self.state.get('version') != REVIEW_VERSION or self.state['input_sha256'] != digest(self.prompt):
                    raise PreparationError('The frozen re-research input changed.')
                self.plan = self.state['plan']
            else:
                self.plan = await asyncio.to_thread(plan_for, self.engine, self.run, self.stage, self.prompt)
                self.state = {'version': REVIEW_VERSION, 'input_sha256': digest(self.prompt), 'plan': self.plan, 'jobs': {}}
            validate_plan(self.plan, body)
            expected = (self.stage.get('preparation') or {}).get('plan_sha256')
            if not expected or summary(self.plan)['plan_sha256'] != expected:
                raise PreparationError('The admitted re-research plan changed.')
            await self.persist()
            nodes = []; known_claims = {c['id'] for c in self.plan['claim_catalog']}; receipts={}
            for part in self.plan['parts']:
                response, usage = await self.call('review-'+part['id'], map_request(self.plan, part), 'research_review')
                trace = usage.get('execution', {})
                if not trace.get('searched') or not trace.get('read_sources') or trace.get('unexpected_tools'):
                    raise PreparationError('Fresh search and source-reading tool activity was not confirmed.')
                await self.progress('checking_sources', part['id'])
                try:
                    node = parse_map(response, part['id'], part['text'], known_claims, self.plan['claim_catalog'])
                except SchemaRepairNeeded as error:
                    repair = ('Repair the structured response below using only the supplied material. Do not search, '
                        'invent facts, or add source URLs. Preserve every existing field value, finding, quote and limitation VERBATIM. '
                        'You may add missing fields, especially prior_assessments. Keep list-valued fields unchanged. '
                        'Supply a separate reasoned assessment for each linked prior claim; do not copy the finding status blindly. '
                        'Return only the corrected complete JSON object. Validation failure: '+str(error)+'\n'+MAP_INSTRUCTIONS.replace(
                        'Actually search and read primary sources, investigate contrary evidence and conditions; do not merely summarize.',
                        'Do not use tools; repair the existing findings using the recorded passages.')+
                        envelope(encoded({'brief':self.plan['brief'],'prior_claims':self.plan['claim_catalog'],
                        'part_id':part['id'],'original_part':part['text'],'recorded_response':response})))
                    fixed,_=await self.call('repair-'+part['id'],repair,'review_merge')
                    if set(citations(fixed))-set(citations(response)):
                        raise PreparationError('Structured repair introduced a new source URL.')
                    validate_repair(response,fixed)
                    node=parse_map(fixed,part['id'],part['text'],known_claims,self.plan['claim_catalog'])
                await self.check_sources(node, part['id'], receipts)
                nodes.append(node)
            ids = [p['id'] for p in self.plan['parts']]
            finding_ids = [f['id'] for node in nodes for f in node['findings']]
            request = MERGE_INSTRUCTIONS + VERIFICATION_INSTRUCTIONS + envelope(encoded({'brief': self.plan['brief'], 'coverage': ids,
                'source_sha256': self.plan['source_sha256'], 'prior_claim_register': self.plan['protected_register'],
                'working_findings': nodes, 'source_receipts':receipts}))
            response, _ = await self.call('reconcile', request, 'review_merge')
            report = parse_merge(response, ids, finding_ids)
            allowed = set(citations(body)) | {s['url'] for n in nodes for f in n['findings'] for s in f['sources']}
            if set(citations(report))-allowed: raise PreparationError('Reconciliation invented an unprovided source URL.')
            matched = sum(r['verification'] == 'passage_matched_not_fact_checked' for r in receipts.values())
            unknown = len(receipts) - matched
            if unknown:
                report += ('\n\n## Source verification / Kaynak doğrulama\n\n'
                    + str(matched) + '/' + str(len(receipts)) + ' source passages matched. '
                    + str(unknown) + ' could not be verified. Affected findings remain unverified. '
                    'Unmatched or unavailable sources are not proof that a claim is true or false. '
                    'Eşleşmeyen veya erişilemeyen kaynaklarla ilişkili bulgular doğrulanamadı; özgün değerlendirmeler ve alıntılar aşağıda korunuyor.\n')
            shared_context = sum(f.get('original_anchor', {}).get('scope') == 'claim_catalog'
                                 for node in nodes for f in node['findings'])
            if shared_context:
                report += ('\n\n## Original passage scope / Özgün alıntının kapsamı\n\n'
                    + str(shared_context) + ' findings quote the supplied claim catalog rather than their current evidence part. '
                    'They remain unverified and do not establish part coverage. Exact passages and their scope are retained below. '
                    'Bu bulguların alıntıları ortak iddia listesinden geliyor; ilgili parçanın kanıtı sayılmadı ve doğrulanmamış olarak korundu.\n')
            # These appendices are deterministic: the model cannot silently omit an accepted finding.
            report += '\n\n## Preserved working evidence\n\n```json\n'+encoded({'findings_by_part':nodes,'source_receipts':receipts})+'\n```\n'
            report += '\n## Claim continuity register\n\n```evidence-ledger\n'+encoded(ledger(self.plan, nodes, self.stage['id']))+'\n```\n'
            report += '\nInput byte coverage and source-passage matching were checked. Semantic completeness and factual truth are not guaranteed by those checks. Original evidence, requests and snapshots remain available.\n'
            await self.progress('completed')
            return report, {'account_review': True, 'calls': len(self.usages), 'measurements': self.usages,
                'coverage': ids, 'source_sha256': self.plan['source_sha256'],
                'source_passages_matched': matched, 'source_passages_unverified': unknown,
                'source_verification_policy': VERIFICATION_POLICY}
        except PreparationError as exc:
            raise ServiceError(str(exc), 409, kind='integrity_error') from exc


async def execute(engine, run, stage, prompt):
    return await ReviewRunner(engine, run, stage, prompt).execute()
