import copy
import json
import sys
from pathlib import Path
import httpx
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packet_markdown import ledger_blocks, ledger_claims, compact_claims, expand_claims
from research_rules import ResearchRules
from token_budget import TokenBudget, MARKDOWN_TRANSPORT
from workflow import report_packet, digest, prompt_for
from engine import Engine, ServiceError, AccountProvider
from test_evidence_protocol import run, finish, claim
from test_workflow import engine, create, add, settle


def sample():
    r=run()
    for i in range(3):finish(r,i,[claim(ident=r['stages'][i]['id']+':C001')])
    return report_packet(r,r['stages'][3])


def budget(count=50,limit=100):
    return {'estimated_tokens':count,'counted_tokens':count,'automatic_input_limit':limit,'utilization':count/limit,
            'token_margin':{'calibration_fingerprint':'f'*64}}


@pytest.mark.parametrize('prefix',[
    '```mermaid\ngraph TD; A --> B;\n```\nProse\n',
    '```python\nprint("```evidence-ledger")\n```\n',
    '~~~~text\n```evidence-ledger\nnot JSON\n```\n~~~~\n',
    '````text\n```json\n{}\n```\n````\n',
])
def test_other_fence_closers_never_swallow_the_ledger(prefix):
    content=prefix+'```evidence-ledger\n'+json.dumps({'claims':[claim()],'blind_spots':['a','b','c']})+'\n```'
    reports=[{'stage':'research_gemini','content':content}]
    assert len(ledger_claims(reports))==1
    assert len(ledger_blocks(content))==1


def test_identical_ledger_repetition_is_referenced_but_conflicting_ledgers_are_not():
    body=json.dumps({'claims':[claim()],'blind_spots':['a','b','c']})
    block='```evidence-ledger\n'+body+'\n```\n'
    report={'stage':'research_gemini','content':block*2}
    assert len(ledger_blocks(report['content']))==1
    assert len(ledger_claims([report]))==1
    report['content']+=block.replace('supported','disputed')
    assert not ledger_claims([report])


def test_ambiguous_claim_ids_are_not_collapsed_or_silently_selected():
    p=sample();report=p['reports'][0]
    c=claim();conflict=dict(c,status='disputed')
    report['content']='```evidence-ledger\n'+json.dumps({'claims':[c,conflict],'blind_spots':['a','b','c']})+'\n```'
    assert not ledger_claims([report])
    result=ResearchRules().evaluate(p,budget())
    assert 'ECA-005' in {f['id'] for f in result['findings']}
    assert not result['blocked']


def test_malformed_id_cannot_crash_policy_preview():
    p=sample()
    p['reports'][0]['content']='```evidence-ledger\n'+json.dumps({'claims':[claim(ident=['invalid'])],'blind_spots':[]})+'\n```'
    result=ResearchRules().evaluate(p,budget())
    assert 'ECA-005' in {f['id'] for f in result['findings']}


def test_repetition_never_deletes_reports_evidence_or_near_duplicate_urls():
    p=sample();p['reports'][1]['content']=p['reports'][0]['content']
    p['reports'][0]['citations']=['https://example.org/x','https://example.org/x?version=2']
    p['reports'][1]['citations']=['https://example.org/x']
    evidence={'name':'proof','content':'original','sha256':digest('original')}
    for r in p['reports'][:2]:r['evidence']=[evidence.copy()]
    before=copy.deepcopy(p);result=ResearchRules().evaluate(p,budget())
    findings={f['id']:f for f in result['findings']}
    assert {'ECA-001','ECA-003','ECA-004'}<=findings.keys()
    assert findings['ECA-001']['evidence']['duplicate_source_entries']==1
    assert not result['blocked'] and p==before


def test_conflicting_statuses_and_unknown_dates_warn_without_truth_adjudication():
    p=sample();c=p['evidence_register']['claims'][0]
    c['assessments'].append(dict(c['assessments'][0],status='rejected',counter_evidence='Unresolved opposing evidence'))
    p['reports'][0].update(researched_at=None,provenance='web_deep_research_import')
    before=copy.deepcopy(p);result=ResearchRules().evaluate(p,budget())
    assert {'ECA-006','ECA-008'}<={f['id'] for f in result['findings']}
    assert not result['blocked'] and result['factual_verification'] is False and p==before


def test_corrupted_attachment_blocks_before_any_call():
    p=sample();p['reports'][0]['evidence']=[{'content':'changed','sha256':digest('original')}]
    result=ResearchRules().evaluate(p,budget())
    assert result['blocked'] and result['block_status']=='integrity_error'


@pytest.mark.parametrize('count,blocked,rule',[(84,False,None),(85,False,'ECA-010'),(100,False,'ECA-010'),(101,True,'ECA-011')])
def test_admission_boundaries(count,blocked,rule):
    result=ResearchRules().evaluate(sample(),budget(count))
    assert result['blocked']==blocked
    if rule:assert rule in {f['id'] for f in result['findings']}


def test_provider_limits_do_not_raise_chatgpt_and_require_bound_calibration():
    b=TokenBudget(len,{'Claude':{'multiplier':1.95,'overhead_tokens':4096,'calibration_fingerprint':'f'*64}})
    e=Engine(None,None,None,len,180000,budget=b,input_limits={'Claude':240000})
    assert e.measure_input('text',{'provider':'ChatGPT'})['automatic_input_limit']==180000
    assert e.measure_input('text',{'provider':'Claude'})['automatic_input_limit']==240000
    with pytest.raises(ValueError,match='calibration'):
        Engine(None,None,None,len,180000,budget=TokenBudget(len),input_limits={'Claude':240000})


def test_reference_roundtrip_after_mermaid_preserves_rejection_history():
    p=sample();reports=p['reports'];claims=p['evidence_register']['claims']
    reports[0]['content']='```mermaid\ngraph TD\n```\n'+reports[0]['content']
    indexed=compact_claims(claims,reports)
    assert 'report_claim' in indexed[0]['assessments'][0]
    assert expand_claims(indexed,reports)==claims


@pytest.mark.asyncio
async def test_changed_account_runtime_never_sends_calibrated_request(tmp_path,monkeypatch):
    requests=[]
    def handler(req):
        requests.append(req.method)
        return httpx.Response(200,json={'prompt_formats':[MARKDOWN_TRANSPORT],'runtime_fingerprints':{'chatgpt-account':'b'*64}})
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr('engine.httpx.AsyncClient',lambda **kwargs:client)
    key=tmp_path/'key';key.write_text('isolated-key')
    with pytest.raises(ServiceError) as exc:
        await AccountProvider(key).synthesize({'provider':'ChatGPT','account_input_format':MARKDOWN_TRANSPORT,
            'input_budget':{'token_margin':{'calibration_fingerprint':'a'*64}}},'full packet')
    assert exc.value.kind=='calibration_required' and requests==['GET']
    assert exc.value.policy['findings'][0]['id']=='ECA-015'


@pytest.mark.asyncio
async def test_rules_are_recorded_and_overflow_never_retries(engine):
    engine.budget=TokenBudget(len);engine.token_limit=20
    r=await create(engine)
    for s in r['stages'][:5]:await add(engine,r['id'],s['id'])
    await settle(engine)
    r=await engine.get(r['id'])
    for s in r['stages'][5:7]:
        assert s['status']=='context_limit' and s['attempts']==0 and s['next_retry_at'] is None
        assert s['policy']['blocked'] and s['policy_events'][-1]['event']=='before_submit'
    assert not engine.provider.calls


@pytest.mark.asyncio
async def test_calibration_failure_is_recorded_and_never_automatically_retried(engine):
    async def mismatch(stage,prompt):
        policy=ResearchRules.provider_check('a'*64,'b'*64)
        raise ServiceError('Calibration changed',503,kind='calibration_required',policy=policy)
    engine.provider.synthesize=mismatch
    r=await create(engine)
    for s in r['stages'][:5]:await add(engine,r['id'],s['id'])
    await settle(engine);r=await engine.get(r['id'])
    for s in r['stages'][5:7]:
        assert s['status']=='calibration_required' and s['next_retry_at'] is None
        assert s['policy_events'][-1]['findings'][0]['id']=='ECA-015'
