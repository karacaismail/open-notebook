"""Module boundaries: opt-in, auth, persistence, busy guard and source isolation."""
import asyncio
import importlib.util
import json
from pathlib import Path

from fastapi import FastAPI
import httpx
import pytest

from api.routers import modules as routes
from open_notebook.modules.registry import ModuleError, Registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def registry(tmp_path, monkeypatch):
    root = tmp_path/'modules'
    for mid in ('account-models','multi-model-research','local-workspace'):
        dest = root/mid
        dest.mkdir(parents=True)
        (dest/'module.json').write_bytes((ROOT/'modules'/mid/'module.json').read_bytes())
    (root/'installed.json').write_text(json.dumps(['account-models','multi-model-research','local-workspace']))
    monkeypatch.delenv('OPEN_NOTEBOOK_MODULES', raising=False)
    monkeypatch.setenv('OPEN_NOTEBOOK_MODULE_DIR',str(root))
    monkeypatch.setenv('OPEN_NOTEBOOK_MODULE_STATE',str(tmp_path/'state/modules.json'))
    return Registry()


def test_default_is_opt_in_and_catalog_never_exposes_service_secrets(registry,monkeypatch):
    monkeypatch.setenv('LOCAL_RESEARCH_KEY','private-sentinel')
    research=next(x for x in registry.catalog() if x['id']=='multi-model-research')
    assert research['installed'] and not research['enabled']
    assert 'private-sentinel' not in json.dumps(registry.catalog())
    assert 'key_env' not in json.dumps(registry.catalog())
    with pytest.raises(ModuleError):registry.require_enabled('multi-model-research')


def test_enable_persists_across_registry_instances_and_disable_preserves_data(registry):
    sentinel=registry.state.parent.parent/'report.md';sentinel.write_text('immutable evidence')
    registry.set_enabled('multi-model-research',True)
    assert 'multi-model-research' in Registry().enabled()
    registry.set_enabled('multi-model-research',False)
    assert 'multi-model-research' not in Registry().enabled()
    assert sentinel.read_text()=='immutable evidence'
    assert registry.state.stat().st_mode & 0o777 == 0o600


def test_build_module_cannot_be_runtime_disabled(registry):
    with pytest.raises(ModuleError):registry.set_enabled('local-workspace',False)


def test_unknown_or_uninstalled_module_rejected(registry):
    with pytest.raises(ModuleError):registry.set_enabled('missing',True)
    (registry.root/'installed.json').write_text('[]')
    with pytest.raises(ModuleError):Registry().set_enabled('multi-model-research',True)


def test_bad_configuration_fails_closed(registry):
    registry.state.parent.mkdir(parents=True)
    registry.state.write_text('{broken')
    with pytest.raises(ModuleError):registry.enabled()


@pytest.mark.parametrize('mutation',['cycle','alias','directory','dependency'])
def test_invalid_manifest_graph_is_rejected(registry,mutation):
    path=registry.root/'account-models/module.json';data=json.loads(path.read_text())
    if mutation=='cycle':data['dependencies']=['multi-model-research']
    if mutation=='alias':data['service']={'url_env':'TEST_URL','key_env':'TEST_KEY','proxy_alias':'notebooks'}
    if mutation=='directory':data['id']='different'
    if mutation=='dependency':data['dependencies']=['missing']
    path.write_text(json.dumps(data))
    with pytest.raises(ModuleError):Registry()


@pytest.mark.asyncio
async def test_exclusive_update_waits_for_proxy_lease(registry):
    entered=asyncio.Event()
    async def writer():
        async with registry.lock(exclusive=True):entered.set()
    async with registry.lock():
        task=asyncio.create_task(writer())
        await asyncio.sleep(.1)
        assert not entered.is_set()
    await asyncio.wait_for(task,1)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_catalog_and_routes_inherit_auth_and_disable_gate(registry,monkeypatch):
    # Real auth middleware, isolated app: no DB, no provider traffic.
    from api.auth import PasswordAuthMiddleware
    monkeypatch.setenv('OPEN_NOTEBOOK_PASSWORD','test-password')
    app=FastAPI();app.add_middleware(PasswordAuthMiddleware)
    routes.register_module_routes(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/api/modules')).status_code==401
        assert (await client.get('/api/research/runs')).status_code==401
        client.headers['Authorization']='Bearer test-password'
        assert (await client.get('/api/modules')).status_code==200
        assert (await client.get('/api/research/runs')).status_code==404
        assert (await client.get('/api/modules/multi-model-research/service/runs')).status_code==404
        assert (await client.put('/api/modules/unknown',json={'enabled':True})).status_code==404


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['active','pending','offline','idle'])
async def test_disable_guard_fails_closed_and_keeps_enabled(registry,monkeypatch,mode):
    registry.set_enabled('multi-model-research',True)
    monkeypatch.setenv('LOCAL_RESEARCH_URL','http://sidecar')
    monkeypatch.setenv('LOCAL_RESEARCH_KEY','secret')
    real_client=httpx.AsyncClient
    def handler(request):
        if mode=='offline':raise httpx.ConnectError('unreachable',request=request)
        if request.url.path=='/health':return httpx.Response(200,json={'status':'healthy','active_synthesis':int(mode=='active')})
        return httpx.Response(200,json=[{'status':'needs_attention' if mode=='pending' else 'paused'}])
    monkeypatch.setattr(routes.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    if mode=='idle':
        await routes.configure_module('multi-model-research',routes.ModuleUpdate(enabled=False))
        assert 'multi-model-research' not in registry.enabled()
    else:
        with pytest.raises(Exception) as error:await routes.configure_module('multi-model-research',routes.ModuleUpdate(enabled=False))
        assert error.value.status_code in (409,503)
        assert 'multi-model-research' in registry.enabled()


@pytest.mark.asyncio
async def test_proxy_preserves_idempotency_but_not_client_authorization(registry,monkeypatch):
    registry.set_enabled('multi-model-research',True)
    monkeypatch.setenv('LOCAL_RESEARCH_URL','http://sidecar')
    monkeypatch.setenv('LOCAL_RESEARCH_KEY','sidecar-key')
    real_client=httpx.AsyncClient;captured=[]
    def handler(request):
        captured.append(request)
        return httpx.Response(201,json={'ok':True},headers={'Cache-Control':'no-store'})
    monkeypatch.setattr(routes.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    app=FastAPI();routes.register_module_routes(app)
    async with real_client(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        result=await client.post('/api/research/runs',json={'question':'test'},headers={'Authorization':'Bearer browser-token','Idempotency-Key':'one-operation'})
        assert result.status_code==201
        assert captured[0].headers['authorization']=='Bearer sidecar-key'
        assert captured[0].headers['idempotency-key']=='one-operation'
        assert json.loads(captured[0].content)=={'question':'test'}


def test_overlay_preflight_does_not_modify_checkout_or_destination(tmp_path):
    spec=importlib.util.spec_from_file_location('prepare',ROOT/'scripts/prepare_modules.py')
    script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    root=tmp_path/'source';mod=root/'modules/theme';mod.mkdir(parents=True)
    (mod/'module.json').write_text(json.dumps({'id':'theme','dependencies':[]}))
    (mod/'overlay.json').write_text(json.dumps([{'target':'file.txt','sha256_before':'wrong'}]))
    (root/'file.txt').write_text('upstream improvement')
    output=tmp_path/'build'
    with pytest.raises(ValueError,match='Upstream file changed'):script.prepare(root,output,['theme'])
    assert not output.exists()
    assert (root/'file.txt').read_text()=='upstream improvement'
