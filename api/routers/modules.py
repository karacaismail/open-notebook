"""Authenticated module catalog, lifecycle gate and fixed-origin service proxy."""
import asyncio
import os
import re
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from open_notebook.modules.registry import ModuleError, Registry

router = APIRouter()


def registry() -> Registry:
    try:
        return Registry()
    except Exception as exc:
        # Keep core notebook routes available even if an optional manifest is bad.
        raise HTTPException(503, 'Module configuration could not be loaded.') from exc


def service_config(item):
    spec = item.service
    if not spec:
        raise ModuleError('This module has no HTTP service', 404)
    base = os.environ.get(spec.url_env, '').rstrip('/')
    key = os.environ.get(spec.key_env, '')
    url = urlsplit(base)
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or not key:
        raise ModuleError('Module service URL and authentication key must be configured.', 503)
    return base, {'Authorization': 'Bearer ' + key}


async def assert_idle(item):
    if not item.service:
        return
    base, headers = service_config(item)
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            response = await client.get(base+'/'+item.service.health_path, headers=headers)
            response.raise_for_status()
            health = response.json()
            if health.get('status') != 'healthy' or any(health.get(field) for field in item.service.busy_fields):
                raise ModuleError('Module has active work. Wait for completion before disabling.')
            if item.service.pending_path:
                response = await client.get(base+'/'+item.service.pending_path, headers=headers)
                response.raise_for_status()
                runs = response.json()
                if not isinstance(runs, list):
                    raise ModuleError('Could not verify pending jobs; module remains enabled.')
                if any(run.get('status') not in ('completed','paused') for run in runs):
                    raise ModuleError('Pause unfinished research runs before disabling this module.')
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise ModuleError('Could not verify that the service is idle; module remains enabled.', 503) from exc


@router.get('/modules')
async def list_modules():
    try:
        return await asyncio.to_thread(registry().catalog)
    except ModuleError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


class ModuleUpdate(BaseModel):
    enabled: bool


@router.put('/modules/{module_id}')
async def configure_module(module_id: str, body: ModuleUpdate):
    reg = registry()
    try:
        async with reg.lock(exclusive=True):
            item = reg.get(module_id)
            if not body.enabled and module_id in reg.enabled():
                await assert_idle(item)
            if body.enabled and item.service:
                service_config(item)
            await asyncio.to_thread(reg.set_enabled, module_id, body.enabled)
            return reg.catalog()
    except ModuleError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


async def proxy(module_id: str, path: str, request: Request):
    if not re.fullmatch(r'[a-zA-Z0-9_/-]+', path) or '..' in path:
        raise HTTPException(400, 'Invalid module service path')
    reg = registry()
    try:
        async with reg.lock():
            item = reg.require_enabled(module_id)
            base, headers = service_config(item)
            body = await request.body()
            if len(body) > 32 * 1024 * 1024:
                raise HTTPException(413, 'Module upload is too large')
            for name in ('content-type', 'idempotency-key'):
                if request.headers.get(name):
                    headers[name] = request.headers[name]
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                result = await client.request(request.method, base+'/'+path, params=request.query_params, content=body, headers=headers)
            response_headers = {name:result.headers[name] for name in ('content-type','content-disposition','cache-control') if name in result.headers}
            # Do not let a sidecar redirect a browser outside the authenticated proxy.
            if 300 <= result.status_code < 400:
                raise HTTPException(502, 'Unexpected module service redirect')
            return Response(result.content, status_code=result.status_code, headers=response_headers)
    except ModuleError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except httpx.RequestError as exc:
        raise HTTPException(503, 'The local module service could not be reached.') from exc


@router.api_route('/modules/{module_id}/service/{path:path}', methods=['GET','POST'])
async def module_proxy(module_id: str, path: str, request: Request):
    return await proxy(module_id, path, request)


def register_module_routes(app):
    app.include_router(router, prefix='/api', tags=['modules'])
    # Legacy URLs are manifest-owned. No research-specific route in core.
    try:
        items = Registry().manifests.values()
    except Exception:
        return
    for item in items:
        if item.service and item.service.proxy_alias:
            def endpoint(mid):
                async def handler(path: str, request: Request):
                    return await proxy(mid, path, request)
                return handler
            app.add_api_route('/api/'+item.service.proxy_alias+'/{path:path}', endpoint(item.id), methods=['GET','POST'], tags=['modules'])
