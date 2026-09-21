import asyncio
import pytest
from engine import ServiceError
from test_workflow import engine, create, add, settle
from workflow import STAGES

async def parallel(engine):
    for sid in ('synthesis_chatgpt','synthesis_claude'):engine.provider.gates[sid]=asyncio.Event()
    run=await create(engine)
    for sid,_,rnd,_ in STAGES:
        if rnd<=2:await add(engine,run['id'],sid)
    for _ in range(100):
        run=await engine.get(run['id'])
        if sum(bool(s.get('request_dispatched')) for s in run['stages'])==2:return run
        await asyncio.sleep(.01)
    assert False

async def control(engine,run,sid,action):
    await engine.stage_action(run['id'],sid,action,engine.stage_snapshot(run,engine.stage(run,sid)))
    await asyncio.gather(*engine.control_tasks.values())
    return await engine.get(run['id'])

@pytest.mark.asyncio
async def test_stage_stop_cancels_only_target_and_preserves_sibling(engine):
    cancelled=[]
    async def cancel(s):cancelled.append(s['id'])
    engine.provider.cancel=cancel
    run=await parallel(engine);rid=run['id'];saved=[s['report'] for s in run['stages'][:5]]
    run=await control(engine,run,'synthesis_chatgpt','stop')
    assert cancelled==['synthesis_chatgpt'] and not run['paused']
    assert engine.stage(run,'synthesis_claude')['status']=='running'
    assert engine.stage(run,'synthesis_chatgpt')['control_state']=='stopped'
    assert not await engine.sweep_retries()
    await engine.kick(rid)
    assert len(engine.provider.calls)==2
    engine.provider.gates['synthesis_claude'].set();await settle(engine)
    assert engine.stage(await engine.get(rid),'final_chatgpt')['status']=='pending'
    engine.provider.gates['synthesis_chatgpt'].set()
    run=await engine.get(rid);await control(engine,run,'synthesis_chatgpt','resume');await settle(engine)
    run=await engine.get(rid);assert run['status']=='completed'
    assert [s['report'] for s in run['stages'][:5]]==saved
    assert [c[0] for c in engine.provider.calls].count('synthesis_claude')==1

@pytest.mark.asyncio
async def test_retry_while_sibling_running_and_snapshot_ignores_sibling_progress(engine):
    run=await parallel(engine);rid=run['id']
    engine.provider.fail.add('synthesis_chatgpt');engine.provider.gates['synthesis_chatgpt'].set()
    await asyncio.sleep(.06);run=await engine.get(rid)
    assert engine.stage(run,'synthesis_chatgpt')['status']=='failed'
    snapshot=engine.stage_snapshot(run,engine.stage(run,'synthesis_chatgpt'))
    engine.provider.fail.clear()
    await engine.stage_action(rid,'synthesis_chatgpt','retry',snapshot)
    await asyncio.sleep(.04)
    run=await engine.get(rid)
    assert engine.stage(run,'synthesis_chatgpt')['status']=='completed'
    assert engine.stage(run,'synthesis_claude')['status']=='running'
    assert [c[0] for c in engine.provider.calls].count('synthesis_claude')==1
    with pytest.raises(ServiceError):await engine.stage_action(rid,'synthesis_chatgpt','retry',snapshot)

@pytest.mark.asyncio
async def test_hold_survives_restart_and_whole_resume_does_not_clear_it(engine):
    run=await create(engine);rid=run['id']
    run=await control(engine,run,'research_chatgpt','cancel')
    await engine.recover();run=await engine.get(rid)
    assert engine.stage(run,'research_chatgpt')['control_state']=='cancelled'
    await engine.action(rid,'pause');await engine.action(rid,'resume')
    run=await engine.get(rid);assert engine.stage(run,'research_chatgpt')['control_state']=='cancelled'
    with pytest.raises(ServiceError):await engine.stage_action(rid,'research_chatgpt','resume')
    run=await control(engine,run,'research_chatgpt','restore')
    assert engine.stage(run,'research_chatgpt')['status']=='waiting_input'

@pytest.mark.asyncio
async def test_stage_route_requires_fresh_confirmation(engine,monkeypatch):
    import httpx,server
    monkeypatch.setattr(server,'ENGINE',engine)
    run=await create(engine);sid='research_chatgpt'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as c:
        url=f"/runs/{run['id']}/stages/{sid}/actions/pause"
        assert (await c.post(url,json={})).status_code==401
        c.headers['Authorization']='Bearer '+server.KEY
        assert (await c.post(url,json={})).status_code==409
        snap=engine.stage_snapshot(run,engine.stage(run,sid))
        assert (await c.post(url,json={'expected_state':snap})).status_code==200
        await asyncio.gather(*engine.control_tasks.values())
        assert (await c.post(url,json={'expected_state':snap})).status_code==409

@pytest.mark.asyncio
async def test_import_into_paused_stage_clears_hold_without_losing_report(engine):
    run=await create(engine);rid=run['id'];sid='research_chatgpt'
    run=await control(engine,run,sid,'pause')
    await add(engine,rid,sid)
    stage=engine.stage(await engine.get(rid),sid)
    assert stage['status']=='completed' and not stage['control_state'] and stage['report']

@pytest.mark.asyncio
async def test_cancelled_stage_cannot_be_bypassed_by_import(engine):
    run=await create(engine);rid=run['id'];sid='research_chatgpt'
    await control(engine,run,sid,'cancel')
    with pytest.raises(ServiceError):await add(engine,rid,sid)
