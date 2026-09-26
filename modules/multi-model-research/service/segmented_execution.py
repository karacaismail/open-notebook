"""Durable account-only multi-pass execution with exhaustive source coverage."""
import asyncio
import copy
import json
import uuid
from pathlib import Path

from context_compaction import expand_prompt
from context_preparation import (VERSION, PreparationError, digest, envelope, parse_result,
                                 plan_segments, render_segment, result_instructions, validate_plan)
from packet_markdown import evidence_body
import synthesis_register as registry


def task_prefix(prompt):
    body,_=evidence_body(prompt)
    return prompt[:-len(body)]


def plan_for(engine, run, stage, prompt):
    original=expand_prompt(prompt); body,_=evidence_body(original)
    members=[s for s in run['stages'] if s['round']==stage['round'] and
             s.get('account_profile')==stage.get('account_profile') and s['mode']=='account'] or [stage]
    # All peer instructions are counted, but evidence boundaries are shared exactly.
    from workflow import prompt_for, report_packet
    prefixes=[(s,task_prefix(prompt_for(run,s))) for s in members]
    instructions=result_instructions(['P999999'])
    def fits(value):
        return all(engine.measure_input(prefix+instructions+value, peer)['utilization'] <= .55
                   for peer,prefix in prefixes)
    plan=plan_segments(body,fits)
    if len(plan['parts'])+1>64:
        raise PreparationError('The evidence and final synthesis exceed the multi-pass call limit.')
    packet=report_packet(run,stage)
    plan['protected_register']=packet.get('evidence_register',{})
    plan['source_inventory']=sorted({url for report in packet['reports'] for url in report['citations']})
    pin=envelope(json.dumps({'evidence_register':plan['protected_register'],'source_inventory':plan['source_inventory']},ensure_ascii=False,separators=(',',':')))
    if not all(engine.measure_input(prefix+result_instructions(['P1'],final=True)+pin,peer)['utilization'] < .8 for peer,prefix in prefixes):
        # Every full record is inspected in a separate bounded pass. The parent
        # sees a clearly scoped catalog and those reports, not a silent truncation.
        plan['register_mode']=registry.VERSION
        plan['register_catalog']=registry.catalog(plan['protected_register'])
        plan['register_brief']={k:run.get(k) for k in ('question','scope','language','as_of')}
        claim_ids=[c['id'] for c in plan['protected_register']['claims']]
        def catalog_fits():
            pin=envelope(json.dumps({'evidence_register':registry.parent_register(plan),
                'source_inventory':plan['source_inventory']},ensure_ascii=False,separators=(',',':')))
            return all(engine.measure_input(prefix+result_instructions(['P1'],final=True)+registry.instructions(claim_ids)+pin,peer)['utilization'] < .8
                       for peer,prefix in prefixes)
        if not catalog_fits():
            plan['register_catalog_mode']='lineage-only'
            plan['register_catalog']=registry.catalog(plan['protected_register'],include_statements=False)
            if not catalog_fits():
                raise PreparationError('Even the complete claim lineage exceeds the parent budget; nothing was removed.')
        def register_fits(batch):
            return all(engine.measure_input(prefix+result_instructions([batch['id']])+
                registry.instructions(batch['claim_ids'])+registry.render_batch(batch),peer)['utilization'] <= .65
                for peer,prefix in prefixes)
        plan['register_parts']=registry.plan_batches(plan['protected_register'],plan['register_brief'],register_fits)
        if len(plan['parts'])+len(plan['register_parts'])+1>64:
            raise PreparationError('Evidence, complete claim batches and final synthesis exceed the multi-pass call limit.')
    # Instructions vary slightly with identifiers. Verify the actual final part prompts.
    for part in plan['parts']:
        for peer,prefix in prefixes:
            if not engine.measure_input(prefix+result_instructions([part['id']])+render_segment(part,plan),peer)['fits']:
                raise PreparationError('A measured evidence part no longer fits.')
    return plan


def summary(plan):
    result={'plan_sha256':digest(json.dumps(plan,ensure_ascii=False,sort_keys=True)),
            'version':VERSION,'source_sha256':plan['source_sha256'],'source_bytes':plan['source_bytes'],
            'parts':len(plan['parts']),'coverage_verified':True,'semantic_lossless':False,
            'status':'planned','minimum_calls':len(plan['parts'])+1}
    if plan.get('register_mode'):
        result.update(register_mode=plan['register_mode'],register_parts=len(plan['register_parts']),
                      register_catalog_mode=plan.get('register_catalog_mode','statements'),
                      minimum_calls=len(plan['parts'])+len(plan['register_parts'])+1)
    return result


async def execute(engine, run, stage, prompt):
    # Imported lazily to avoid coupling the planner to the application engine.
    from engine import ServiceError
    path=engine.input_path(run,stage).parent/'segmented-journal.json'
    original=expand_prompt(prompt); body,_=evidence_body(original)
    prefix=task_prefix(original)
    if path.exists():
        state=json.loads(await asyncio.to_thread(path.read_text))
        if state['input_sha256']!=digest(prompt):
            raise ServiceError('Segmented input changed.',409,kind='integrity_error')
        plan=state['plan']
    else:
        plan=await asyncio.to_thread(plan_for,engine,run,stage,prompt)
        state={'version':VERSION,'input_sha256':digest(prompt),'plan':plan,'jobs':{}}
    validate_plan(plan,body)
    registry.validate_plan(plan)
    expected=(stage.get('preparation') or {}).get('plan_sha256')
    if expected and summary(plan)['plan_sha256']!=expected:
        raise ServiceError('Saved preparation plan changed.',409,kind='integrity_error')

    async def persist():
        def write():
            temp=path.with_suffix('.tmp'); temp.touch(mode=0o600)
            temp.write_text(json.dumps(state,ensure_ascii=False));temp.replace(path)
        await asyncio.to_thread(write)
    await persist()
    usages=[]
    from workflow import citations
    allowed_urls=set(citations(body))
    def validated_report(response,ids,claim_ids=()):
        report=parse_result(response,ids)
        registry.validate_response(response,claim_ids)
        if set(citations(report))-allowed_urls:
            raise PreparationError('Intermediate output introduced a source URL absent from its evidence.')
        return report

    def request_text(data,ids,final=False,claim_ids=()):
        return prefix+result_instructions(ids,final=final)+registry.instructions(claim_ids)+data

    async def call(key, data, ids, final=False,claim_ids=()):
        request=request_text(data,ids,final,claim_ids)
        budget=await asyncio.to_thread(engine.measure_input,request,stage)
        if not budget['fits']:
            raise ServiceError('Prepared subrequest exceeds the input budget.',409,kind='context_limit')
        job=state['jobs'].get(key)
        if job:
            if job['input_sha256']!=digest(request):
                raise ServiceError('Saved subrequest changed.',409,kind='integrity_error')
            if job['status']=='completed':
                if digest(job['response'])!=job['response_sha256']:
                    raise ServiceError('Saved response changed.',409,kind='integrity_error')
                usages.append(job['usage'])
                return validated_report(job['response'],ids,claim_ids)
            if job['status']=='in_flight':
                if not hasattr(engine.provider,'recover'):
                    raise ServiceError('A previous subrequest may have completed remotely. It was not repeated.',409,kind='submission_uncertain')
                child=dict(stage,request_id=job['request_id'])
                async with engine.lock:
                    latest=await engine.get(run['id']);current=engine.stage(latest,stage['id'])
                    current['request_id']=child['request_id']
                    await engine.store.save(latest)
                try:
                    recover=getattr(engine.provider,'recover_pending',engine.provider.recover)
                    response,usage=await recover(child,request)
                except ServiceError as exc:
                    if exc.settled:
                        job.update(status='rejected',error=str(exc),error_kind=exc.kind,request_settled=True)
                        await persist()
                    raise
                job.update(status='completed',response=response,response_sha256=digest(response),usage=usage)
                await persist();usages.append(usage)
                return validated_report(response,ids,claim_ids)
        if len(state['jobs'])>=64 and key not in state['jobs']:
            raise ServiceError('Multi-pass call limit reached; completed parts are saved.',409,kind='context_limit')
        child=copy.deepcopy(stage);child.update(request_id=uuid.uuid4().hex,input_budget=budget)
        async with engine.lock:
            latest=await engine.get(run['id']);current=engine.stage(latest,stage['id'])
            if latest.get('control_state') or latest.get('paused') or current.get('control_state'):
                raise ServiceError('Preparation paused; completed parts are saved.',409,kind='interrupted')
            current['request_id']=child['request_id']
            current['preparation']=dict(summary(plan),status='running',completed_calls=sum(j['status']=='completed' for j in state['jobs'].values()),current=key)
            await engine.store.save(latest)
        if job:state.setdefault('attempt_history',[]).append(dict(job,key=key))
        state['jobs'][key]={'status':'in_flight','request_id':child['request_id'],
                            'input_sha256':digest(request),'input':request,'coverage':ids,'budget':budget}
        await persist()
        try:
            response,usage=await engine.provider.synthesize(child,request)
        except ServiceError as exc:
            if exc.settled or exc.kind in ('quota_wait','login_required','research_unavailable','calibration_required'):
                state['jobs'][key].update(status='rejected',error=str(exc),error_kind=exc.kind,request_settled=exc.settled)
                await persist();raise
            raise ServiceError('Subrequest outcome is uncertain; it was not automatically repeated.',409,kind='submission_uncertain') from exc
        except Exception as exc:
            raise ServiceError('Connection ended without a confirmed outcome; the subrequest was not repeated.',409,kind='submission_uncertain') from exc
        # Save even malformed output before validation: it is evidence for recovery.
        state['jobs'][key].update(status='completed',response=response,response_sha256=digest(response),usage=usage)
        await persist();usages.append(usage)
        return validated_report(response,ids,claim_ids)

    try:
        nodes=[]
        for part in plan['parts']:
            result=await call('map-'+part['id'],render_segment(part,plan),[part['id']])
            nodes.append({'ids':[part['id']],'report':result})
        for batch in plan.get('register_parts',[]):
            result=await call('register-'+batch['id'],registry.render_batch(batch),[batch['id']],claim_ids=batch['claim_ids'])
            nodes.append({'ids':[batch['id']],'report':result,'claim_ids':batch['claim_ids']})
        for depth in range(4):
            ids=[ident for n in nodes for ident in n['ids']]
            claim_ids=registry.node_claim_ids(nodes)
            def data(group):
                return envelope(json.dumps({'source_sha256':plan['source_sha256'],
                    'notice':'Intermediate findings from exhaustive source parts; these are not the original full evidence.',
                    'evidence_register':registry.parent_register(plan),
                    'source_inventory':plan.get('source_inventory',[]),'findings':group},ensure_ascii=False,separators=(',',':')))
            merged=data(nodes)
            if engine.measure_input(request_text(merged,ids,stage['round']==4,claim_ids),stage)['utilization']<=.95:
                if plan.get('register_mode') and set(claim_ids)!={c['id'] for c in plan['protected_register']['claims']}:
                    raise PreparationError('Final synthesis omitted a complete claim-record batch.')
                result=await call('final-'+str(depth),merged,ids,final=stage['round']==4,claim_ids=claim_ids)
                async with engine.lock:
                    latest=await engine.get(run['id']);current=engine.stage(latest,stage['id'])
                    current['preparation']=dict(summary(plan),status='completed',completed_calls=len(state['jobs']))
                    await engine.store.save(latest)
                usage={'segmented':True,'calls':len(usages),'measurements':usages,
                       'coverage':ids,'source_sha256':plan['source_sha256']}
                if plan.get('register_mode'):
                    usage.update(register_claim_ids=claim_ids,register_sha256=registry.catalog(plan['protected_register'])['full_register_sha256'])
                    result+='\n\n## Claim-register scope / İddia kaydının kapsamı\n\n'+registry.parent_register(plan)['notice']+'\n\nTam iddia kayıtları ayrı gruplarda incelendi. Özgün kayıtlar ve ara yanıtlar saklanır; son anlatım tüm ham kayıtları tek seferde görmedi. Kapsam kontrolü anlamsal eksiksizlik veya doğruluk garantisi değildir.\n'
                return result,usage
            groups=[];group=[]
            for node in nodes:
                trial=group+[node];covered=[i for n in trial for i in n['ids']]
                if group and engine.measure_input(request_text(data(trial),covered,claim_ids=registry.node_claim_ids(trial)),stage)['utilization']>.7:
                    groups.append(group);group=[]
                group.append(node)
            if group:groups.append(group)
            next_nodes=[]
            for index,group in enumerate(groups):
                ids=[i for n in group for i in n['ids']]
                claim_ids=registry.node_claim_ids(group)
                result=await call(f'reduce-{depth}-{index}',data(group),ids,claim_ids=claim_ids)
                node={'ids':ids,'report':result}
                if claim_ids:node['claim_ids']=claim_ids
                next_nodes.append(node)
            if sum(len(n['report']) for n in next_nodes)>=sum(len(n['report']) for n in nodes):
                raise PreparationError('Intermediate findings did not shrink; no content was truncated.')
            nodes=next_nodes
        raise PreparationError('Bounded multi-pass preparation did not converge.')
    except PreparationError as exc:
        raise ServiceError(str(exc),409,kind='integrity_error') from exc


async def confirm_cancel(engine,run,stage):
    """Called only after the provider confirms stop and the local task has settled."""
    path=engine.input_path(run,stage).parent/'segmented-journal.json'
    if not path.exists():return
    state=json.loads(await asyncio.to_thread(path.read_text))
    changed=False
    for job in state['jobs'].values():
        if job['status']=='in_flight' and job['request_id']==stage.get('request_id'):
            job['status']='cancelled';changed=True
    if changed:
        def write():
            temporary=path.with_suffix('.tmp');temporary.touch(mode=0o600)
            temporary.write_text(json.dumps(state,ensure_ascii=False));temporary.replace(path)
        await asyncio.to_thread(write)
