"""Validated manifests and atomic configuration for bundled modules.

Only trusted source manifests are loaded. They describe UI and fixed-origin HTTP
services; a manifest cannot inject Python imports or install dependencies.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ID = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"


class ModuleError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class FrontendSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry: str = Field(pattern=r"^[a-zA-Z0-9_/-]+\.tsx$")
    route: str = Field(pattern=r"^/[a-z][a-z0-9/-]*$")
    label_key: str
    section: Literal["collect", "process", "create", "manage"] = "process"
    icon: Literal["telescope", "puzzle"] = "puzzle"


class ServiceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    health_path: str = Field(default="health", pattern=r"^[a-zA-Z0-9_/-]+$")
    busy_fields: list[str] = []
    pending_path: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_/-]+$")
    proxy_alias: str | None = Field(default=None, pattern=ID)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    id: str = Field(pattern=ID)
    name: str
    description: str
    version: str
    author: str
    activation: Literal["runtime", "external", "build"]
    dependencies: list[str] = []
    frontend: FrontendSpec | None = None
    service: ServiceSpec | None = None


class Registry:
    def __init__(self, root: Path | None = None, state: Path | None = None):
        self.root = root or Path(os.environ.get("OPEN_NOTEBOOK_MODULE_DIR", "modules"))
        self.state = state or Path(os.environ.get("OPEN_NOTEBOOK_MODULE_STATE", "data/modules.json"))
        self.manifests: dict[str, Manifest] = {}
        for path in sorted(self.root.glob("*/module.json")):
            item = Manifest.model_validate_json(path.read_text())
            if item.id != path.parent.name or item.id in self.manifests:
                raise ModuleError("Module ID must match its unique directory", 503)
            self.manifests[item.id] = item
        self._validate_graph()
        install_file = self.root / "installed.json"
        installed = json.loads(install_file.read_text()) if install_file.exists() else []
        if not isinstance(installed, list) or any(not isinstance(x, str) or x not in self.manifests for x in installed):
            raise ModuleError("Invalid installed module inventory", 503)
        self.installed = set(installed)
        for mid in self.installed:
            if not set(self.manifests[mid].dependencies) <= self.installed:
                raise ModuleError("Installed module dependency missing", 503)

    def _validate_graph(self):
        seen, visiting, aliases, routes = set(), set(), set(), set()
        def visit(mid):
            if mid in visiting or mid not in self.manifests:
                raise ModuleError("Module dependency cycle or unknown dependency", 503)
            if mid in seen:
                return
            visiting.add(mid)
            for dep in self.manifests[mid].dependencies:
                visit(dep)
            visiting.remove(mid)
            seen.add(mid)
        for item in self.manifests.values():
            visit(item.id)
            if item.frontend:
                route = item.frontend.route
                if route in routes or route.split('/')[1] in {'api','settings','sources','notebooks','search','podcasts','login','modules','advanced','transformations'}:
                    raise ModuleError("Module UI route collision", 503)
                routes.add(route)
            if item.service and item.service.proxy_alias:
                alias = item.service.proxy_alias
                if alias in aliases or alias in {'modules','auth','config','models','notebooks','sources','notes','chat','settings','search','podcasts','providers','credentials','commands','embedding','transformations','insights','languages','capabilities','episode-profiles','speaker-profiles'}:
                    raise ModuleError("Module API alias collision", 503)
                aliases.add(alias)

    def enabled(self) -> set[str]:
        try:
            if self.state.exists():
                data = json.loads(self.state.read_text())
                ids = data['enabled']
            else:
                ids = [x.strip() for x in os.environ.get('OPEN_NOTEBOOK_MODULES', '').split(',') if x.strip()]
            if not isinstance(ids, list) or any(not isinstance(x, str) or x not in self.installed for x in ids):
                raise ValueError('Unknown or uninstalled module')
            enabled = set(ids)
            # Build/external installations are fixed; a runtime toggle only gates
            # this application's UI/API, never kills an OS service or removes data.
            enabled |= {mid for mid in self.installed if self.manifests[mid].activation != 'runtime'}
            for mid in enabled:
                if not set(self.manifests[mid].dependencies) <= enabled:
                    raise ValueError('Disabled dependency')
            return enabled
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ModuleError('Module configuration is invalid; restore its last valid copy.', 503) from exc

    def catalog(self):
        enabled = self.enabled()
        return [dict(id=m.id, name=m.name, description=m.description, version=m.version,
                     activation=m.activation, dependencies=m.dependencies,
                     installed=m.id in self.installed, enabled=m.id in enabled,
                     frontend=m.frontend.model_dump() if m.frontend else None)
                for m in self.manifests.values()]

    def get(self, mid: str) -> Manifest:
        if mid not in self.manifests:
            raise ModuleError('Unknown module', 404)
        return self.manifests[mid]

    def require_enabled(self, mid: str) -> Manifest:
        item = self.get(mid)
        if mid not in self.enabled():
            raise ModuleError('Module is disabled or not installed', 404)
        return item

    def set_enabled(self, mid: str, enabled: bool):
        item = self.get(mid)
        if item.activation != 'runtime' or mid not in self.installed:
            raise ModuleError('This module requires an operator installation/build.')
        values = self.enabled()
        if enabled:
            if not set(item.dependencies) <= values:
                raise ModuleError('Enable the required dependencies first.')
            values.add(mid)
        else:
            if any(mid in self.manifests[x].dependencies for x in values - {mid}):
                raise ModuleError('An enabled module depends on this module.')
            values.discard(mid)
        self.state.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.state.parent, prefix='.modules-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'version':1, 'enabled':sorted(values)}, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.state)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @asynccontextmanager
    async def lock(self, exclusive=False):
        """Cross-worker lease: disabling cannot race an admitted proxy request."""
        await asyncio.to_thread(self.state.parent.mkdir, parents=True, exist_ok=True)
        stream = await asyncio.to_thread(open, self.state.with_suffix('.lock'), 'a')
        # Nonblocking polling remains cancellable (no leaked thread holding a lock).
        try:
            while True:
                try:
                    fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.05)
            yield
        finally:
            stream.close()
