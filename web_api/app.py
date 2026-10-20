"""Local HTTP boundary around the existing Agent; no hosted model transport."""
from __future__ import annotations

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import uuid
import zipfile
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
import requests

from local_analyst import Workspace
from local_analyst.agent import AnalystAgent
from local_analyst.providers import CLOUD_MODELS, PRICING, OpenAIProvider, SpendLedger, ProviderBlocked, sol_readiness, redact
from local_analyst.completion import inventory_only
from local_analyst.config import PROJECT_ROOT, resolve_model
from local_analyst.modeling import inspect_rules
from local_analyst.workspace import dump, quote, fingerprint

TERMINAL = {"completed", "incomplete", "blocked", "failed", "cancelled"}
ORIGINS = {"http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001"}
MAX_FILE = 50 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier():
    return uuid.uuid4().hex


def clean(value):
    """Never serialize the backend credential, including in exception messages."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Decimal) or (isinstance(value, int) and abs(value) > 9007199254740991):
        return str(value)
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def encoded(value):
    return dump(clean(value))


class NameInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ConversationInput(BaseModel):
    title: str = Field(default="New analysis", min_length=1, max_length=100)


class PathInput(BaseModel):
    path: str = Field(min_length=1, max_length=2048)
    namespace: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,60}$")


class MessageInput(BaseModel):
    confidential_mode: bool = False
    content: str = Field(min_length=1, max_length=20000)
    provider: Literal["ollama", "openai"] = "ollama"
    approval_id: str | None = None
    reasoning: Literal["low", "medium", "high"] = "low"
    model: str | None = Field(default=None, max_length=100)


class BenchmarkInput(BaseModel):
    confidential_mode: bool = False
    workspace_id: str
    source_id: str
    models: list[Literal['qwen3:8b','qwen3.8:27b','gpt-6-luna','gpt-6.1-sol']] = Field(min_length=1,max_length=4)
    paid_authorized: bool = False
    data_authorized: bool = False
    run_cap: str = '0.25'
    session_cap: str = '2.00'
    total_cap: str = '2.00'
    sol_cap: str = '1.00'


class CloudApprovalInput(BaseModel):
    confidential_mode: bool = False
    model: Literal['gpt-6-luna', 'gpt-6.1-sol']
    paid_authorized: bool = False
    data_authorized: bool = False
    run_cap: str = '0.25'
    session_cap: str = '2.00'
    total_cap: str = '2.00'
    sol_cap: str = '1.00'
    max_requests: int = Field(default=8, ge=1, le=8)


class WorkflowObjectiveInput(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    requirement: str = Field(min_length=1,max_length=4000)
    kind: str = Field(default="financial",max_length=64)
    dependencies: list[str] = Field(default_factory=list,max_length=20)
    required_evidence: list[str] = Field(default_factory=list,max_length=20)
    required_artifacts: list[Literal["excel","pdf","csv"]] = Field(default_factory=list,max_length=3)

class WorkflowInput(BaseModel):
    acceptance_contracts: list[dict] = Field(default_factory=list,max_length=20)
    workspace_id: str
    task: str = Field(min_length=1,max_length=20000)
    source_ids: list[str] = Field(min_length=1,max_length=10)
    mode: Literal["single","comparison","multi"] = "single"
    models: dict[str,str]
    objectives: list[WorkflowObjectiveInput] = Field(min_length=1,max_length=20)
    rule_text: str = Field(default="",max_length=30000)
    formats: list[Literal["excel","pdf","csv"]] = []
    profile: Literal["general","assisted","reference"] = "general"
    role_allocations: dict[str,int] | None = None
    max_requests: int = Field(default=8,ge=1,le=32)
    max_tools: int = Field(default=24,ge=1,le=24)
    max_seconds: int = Field(default=600,ge=1,le=1800)

class SettingsInput(BaseModel):
    execution_mode: Literal["single","comparison","multi"] = "single"
    workflow_models: dict[str,str] = {"analyst":"qwen3.8:27b"}

    confidential_mode: bool = False
    theme: Literal["dark", "light", "system"] = "dark"
    sidebar_width: int = Field(default=248, ge=200, le=360)
    inspector_width: int = Field(default=380, ge=300, le=600)
    sidebar_open: bool = True
    inspector_open: bool = True


class RunView(BaseModel):
    id: str
    workspace_id: str
    conversation_id: str
    status: str
    model: str
    created_at: str
    result: dict[str, Any] | None = None


class EventView(BaseModel):
    seq: int
    run_id: str
    kind: str
    created_at: str
    payload: dict[str, Any]


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, parent TEXT, data TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS records_parent ON records(kind,parent);
          CREATE TABLE IF NOT EXISTS events(run_id TEXT, seq INTEGER, data TEXT NOT NULL, PRIMARY KEY(run_id,seq));
        """)
        self.db.commit()
        Path(path).chmod(0o600)

    def put(self, kind, parent=None, **data):
        data = {"id": identifier(), "created_at": now(), **data}
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?)", (data['id'], kind, parent, encoded(data)))
        return clean(data)

    def get(self, kind, key):
        with self.lock:
            row = self.db.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
        if not row:
            raise HTTPException(404, "Record not found")
        return json.loads(row[0])

    def list(self, kind, parent=None):
        with self.lock:
            if parent is None:
                rows = self.db.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid", (kind,)).fetchall()
            else:
                rows = self.db.execute("SELECT data FROM records WHERE kind=? AND parent=? ORDER BY rowid", (kind, parent)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def patch(self, kind, key, **changes):
        with self.lock, self.db:
            data = {**self.get(kind, key), **changes}
            self.db.execute("UPDATE records SET data=? WHERE kind=? AND id=?", (encoded(data), kind, key))
        return clean(data)

    def event(self, run_id, kind, payload):
        with self.lock, self.db:
            seq = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
            event = {"seq": seq, "run_id": run_id, "kind": kind, "created_at": now(), "payload": clean(payload)}
            self.db.execute("INSERT INTO events VALUES(?,?,?)", (run_id, seq, encoded(event)))
        return event

    def events(self, run_id, after=0):
        with self.lock:
            rows = self.db.execute("SELECT data FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after)).fetchall()
        return [json.loads(r[0]) for r in rows]


class Runtime:
    def __init__(self, manager, key):
        self.key, self.lock = key, threading.RLock()
        self.root = manager.root / "workspaces" / key
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspace = Workspace(self.root, restore=False)
        self.agents = {}
        self.snapshot_lock = threading.Lock()
        self.table_snapshot = []
        self.recovery_errors = []
        for source in manager.store.list('file', key):
            try:
                path = manager.allowed_path(self, source['path'])
                self.workspace.load(path, source['namespace'], persist=False)
            except Exception:
                self.recovery_errors.append(f"Could not reload {source['name']}; select and load the source again.")
        self.refresh_snapshot()

    def refresh_snapshot(self):
        with self.snapshot_lock:
            self.table_snapshot = copy.deepcopy(list(self.workspace.catalog.values()))

    def tables(self):
        with self.snapshot_lock:
            return copy.deepcopy(self.table_snapshot)

    @contextmanager
    def ready(self):
        if not self.lock.acquire(blocking=False):
            raise HTTPException(409, "Workspace is running. Inspect the live tool results or wait for the run to stop.")
        try:
            yield self.workspace
        finally:
            self.lock.release()


class Manager:
    def __init__(self, root, import_roots, agent_factory=AnalystAgent):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.store = Store(self.root / 'sessions.sqlite3')
        self.spend = SpendLedger(self.root / 'spend.sqlite3')
        self.import_roots = [Path(p).resolve() for p in import_roots]
        self.agent_factory = agent_factory
        self.runtimes, self.cancellations = {}, {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='gridline-run')
        self._models, self._model_time = None, 0
        # No in-memory derived dataset survives a server restart.
        for run in self.store.list('run'):
            if run['status'] not in TERMINAL:
                result = {'status': 'incomplete', 'answer': 'The server restarted during this run. Reloaded raw sources are available; recompute derived calculations.'}
                self.store.patch('run', run['id'], status='incomplete', result=result)
                self.store.event(run['id'], 'run_incomplete', {'result': result, 'reason': 'server_restart'})
                self.store.put('message', run['conversation_id'], role='assistant', content=result['answer'], status='incomplete', run_id=run['id'])

        for workflow in self.store.list("workflow"):
            if workflow.get("status")=="running":
                self.store.patch("workflow",workflow["id"],status="BLOCKED",error="SERVER_RESTART: saved evidence retained; no automatic inference/recreation")

    def runtime(self, key):
        self.store.get('workspace', key)
        with self.lock:
            if key not in self.runtimes:
                self.runtimes[key] = Runtime(self, key)
            return self.runtimes[key]

    def active(self, workspace_id):
        return next((r for r in self.store.list('run', workspace_id) if r['status'] not in TERMINAL), None)

    def allowed_path(self, runtime, value, suffixes=('.csv', '.xlsx')):
        try:
            path = Path(value).expanduser().resolve(strict=True)
        except (OSError, ValueError):
            raise HTTPException(400, 'Source file is unavailable')
        roots = [runtime.root / 'uploads', *self.import_roots]
        if path.suffix.lower() not in suffixes or not path.is_file() or any(part.startswith('.') for part in path.name.split('/')):
            raise HTTPException(403, 'This file type is not authorized')
        if not any(path.is_relative_to(p.resolve()) for p in roots):
            raise HTTPException(403, 'Path is outside authorized import folders. Use the file picker to upload an explicit source.')
        if path.stat().st_size > MAX_FILE:
            raise HTTPException(413, 'Source exceeds the 50 MB limit')
        if path.suffix.lower() == '.xlsx':
            try:
                with zipfile.ZipFile(path) as archive:
                    entries = archive.infolist()
                    if len(entries) > 10000 or sum(e.file_size for e in entries) > 250 * 1024 * 1024:
                        raise HTTPException(413, 'Expanded workbook exceeds the local import limit')
            except zipfile.BadZipFile:
                raise HTTPException(400, 'Invalid XLSX workbook')
        return path

    def source(self, runtime, path, namespace=None):
        path = self.allowed_path(runtime, path)
        with runtime.ready() as workspace:
            # New namespace avoids replacing an existing registered source unexpectedly.
            namespace = namespace or 'source_' + identifier()[:8]
            result = workspace.load(path, namespace)
            runtime.refresh_snapshot()
            self.store.put('file', runtime.key, name=path.name, path=str(path), namespace=namespace,
                           sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size)
        return clean(result)

    def models(self):
        import time
        if self._models is not None and time.monotonic() - self._model_time < 20:
            return self._models
        try:
            response = requests.get('http://127.0.0.1:11434/api/tags', timeout=3)
            response.raise_for_status()
            models = []
            for item in response.json().get('models', [])[:20]:
                # A loopback Ollama endpoint can also expose hosted model tags.
                # This adapter has no cloud consent/budget transport: exclude them.
                if item.get('remote_model') or item.get('remote_host') or 'cloud' in item.get('name','').lower():
                    continue
                capabilities = item.get('capabilities')
                model_info={}
                if capabilities is None:
                    shown = requests.post('http://127.0.0.1:11434/api/show', json={'model': item['name']}, timeout=3)
                    shown.raise_for_status()
                    metadata = shown.json()
                    if metadata.get('remote_model') or metadata.get('remote_host'):
                        continue
                    capabilities = metadata.get('capabilities', [])
                    model_info=metadata.get('model_info',{})
                if 'tools' in capabilities:
                    models.append({'id': item['name'], 'provider': 'ollama', 'label': item['name'], 'local': True,'native_context_limit':next((v for k,v in model_info.items() if k.endswith('.context_length')),None)})
            value = {'provider': 'ollama', 'available': True, 'models': models, 'error': None}
        except (requests.RequestException, ValueError, KeyError):
            value = {'provider': 'ollama', 'available': False, 'models': [], 'error': 'Ollama is unavailable. Start it locally; table inventories still work without inference.'}
        value['models'].extend({'id':m,'provider':'openai','label':m,'local':False} for m in CLOUD_MODELS)
        from local_analyst.registry import model_registry
        metadata={m['id']:m for m in model_registry(value['models'],connected=value['available'])}
        value['models']=[{**metadata[m['id']],**m} for m in value['models']]
        value.update({'cloud': {'implemented': True, 'credentials_configured': bool(os.environ.get('OPENAI_API_KEY'))},
                      'default_model': resolve_model(),
                      'usage': {'supported': False, 'api_cost_usd': None, 'budget_usd': None, 'remaining_budget_usd': None, 'reason': 'Cloud requires explicit conversation/source-bound consent and budget. Invoice cost is unavailable; usage-based estimates are recorded per request.'},
                      'response_streaming': False, 'pricing':PRICING, 'spending':self.spend.summary()})
        self._models, self._model_time = value, time.monotonic()
        return clean(value)

    def sol_ready(self):
        luna_attempt=any(r.get('model')=='gpt-6-luna' and r.get('api_requests',0)>0 and r.get('candidate') and (r.get('cloud_usage') or {}).get('input_tokens',0)>0 and not (r.get('cloud_usage') or {}).get('unknown_usage_requests',1)
            for b in self.store.list('benchmark') for r in b.get('results',[]))
        local_attempts={r.get('model') for b in self.store.list('benchmark') for r in b.get('results',[])
            if r.get('model_requests',0)>0 and r.get('model') not in CLOUD_MODELS}
        return sol_readiness() and luna_attempt and {'qwen3:8b','qwen3.8:27b'}.issubset(local_attempts)

    def check_cloud_privacy(self, requested=False):
        if requested or any(s.get("confidential_mode",False) for s in self.store.list("settings")):
            raise HTTPException(409,"CONFIDENTIAL_MODE_UNAVAILABLE: strict minimization is incomplete; cloud requests are blocked")

    def cloud_scope(self, runtime):
        return hashlib.sha256(encoded({'tables':{t:m['version_id'] for t,m in runtime.workspace.catalog.items()},
                           'plans':runtime.workspace.plans}).encode()).hexdigest()

    def guard_agent(self, agent, runtime):
        agent.functions['load_data'] = lambda path, namespace=None: self.source(runtime, path, namespace)
        def rules(path):
            allowed = self.allowed_path(runtime, path, ('.json',))
            if not any(Path(p['path']).resolve() == allowed for p in runtime.workspace.plans.values()):
                raise ValueError('Only a user-registered reviewed plan can be read through the web adapter')
            return inspect_rules(allowed)
        agent.functions['inspect_rules'] = rules

    def start(self, conversation_id, message):
        conversation = self.store.get('conversation', conversation_id)
        runtime = self.runtime(conversation['workspace_id'])
        with self.lock:
            if self.active(runtime.key):
                raise HTTPException(409, 'This workspace already has an active run')
            provider = self.models()
            model = message.model or resolve_model()
            if message.provider=='openai':
                self.check_cloud_privacy(message.confidential_mode)
                if model not in CLOUD_MODELS or not message.approval_id:
                    raise HTTPException(403,'Cloud model, paid approval and data authorization required')
                if model=='gpt-6.1-sol':
                    raise HTTPException(409,'Sol evaluation is gated: complete local/Luna acceptance and independent case validation before requesting a separate paid Sol attempt.')
                approval = self.store.get('cloud_approval',message.approval_id)
                if approval['conversation_id']!=conversation_id or approval['scope']!=self.cloud_scope(runtime):
                    raise HTTPException(403,'Cloud authorization is stale or belongs to another conversation/source version')
                if approval['model']!=model:
                    raise HTTPException(403,'Unapproved model escalation')
                if not os.environ.get('OPENAI_API_KEY'):
                    raise HTTPException(503,'OpenAI credentials are not configured on the backend')
            if message.provider=='ollama' and model not in {m['id'] for m in provider['models'] if m.get('local',True)} and not inventory_only(message.content):
                raise HTTPException(503, 'Select an installed, tool-capable Ollama model. No cloud fallback is enabled.')
            run = self.store.put('run', runtime.key, workspace_id=runtime.key, conversation_id=conversation_id,
                                 model=model, provider=message.provider, approval_id=message.approval_id, reasoning=message.reasoning, status='queued', result=None)
            self.store.put('message', conversation_id, role='user', content=message.content, status='sent', run_id=run['id'])
            self.store.patch('conversation', conversation_id, title=message.content[:65] if conversation['title']=='New analysis' else conversation['title'], model=model, provider=message.provider, reasoning=message.reasoning)
            flag = threading.Event()
            self.cancellations[run['id']] = flag
            self.store.event(run['id'], 'run_queued', {'model': model})
            self.executor.submit(self.execute, runtime, conversation_id, run, clean(message.content), flag)
            return run

    def execute(self, runtime, conversation_id, run, question, flag):
        run_id = run['id']
        current_call = None
        def emit(kind, payload):
            nonlocal current_call
            if kind == 'tool_call_started':
                current_call = payload['call_id']
            self.store.event(run_id, kind, payload)
            if kind == 'tool_result_received':
                self.capture_artifacts(runtime, run_id, payload.get('result', {}))
                current_call = None
        def audit(event):
            if event['kind'] == 'dataset_version':
                runtime.refresh_snapshot()
            if event['kind'] == 'query':
                emit('sql_executed', {'call_id': current_call, 'query_id': event['event_id'],
                                     'sql': event['sql'], 'parameters': event['parameters'],
                                     'result': event['result'], 'result_digest': event['result_digest'],
                                     'dataset_versions': {t: m['version_id'] for t,m in event['dependencies'].items()}})
            if event['kind'] in {'query','schema_profile','schema_inventory','model_run','scenario','quality_check','join_check','export'}:
                emit('evidence_recorded', {'call_id': current_call, 'evidence_id': event['event_id'], 'kind': event['kind']})
        try:
            with runtime.lock:
                if flag.is_set():
                    result = {'status':'incomplete','answer':'Run cancelled before execution.'}
                else:
                    self.store.patch('run', run_id, status='running')
                    emit('run_started', {'model': run['model'], 'provider':run.get('provider','ollama')})
                    agent = runtime.agents.get(conversation_id)
                    if agent is not None and getattr(agent,'_provider_name','ollama')!=run.get('provider','ollama'):
                        agent = None
                    if agent is None:
                        agent = self.agent_factory(runtime.workspace, model=run['model'])
                        # Restore visible context, not old hidden tool results or derived state.
                        previous = [m for m in self.store.list('message', conversation_id) if m['run_id'] != run_id]
                        if previous:
                            agent.history.extend({'role':m['role'],'content':m['content']} for m in previous[-20:])
                            agent.history.append({'role':'system','content':'Visible conversation restored. Raw sources were reloaded, but derived tables and live tool state were not restored. Recompute derived calculations; validate every cited query against current versions.'})
                        runtime.agents[conversation_id] = agent
                    agent.model, agent.on_event, agent.should_cancel = run['model'], emit, flag.is_set
                    agent._provider_name=run.get('provider','ollama')
                    agent.provider=None
                    if agent._provider_name=='openai':
                        approval=self.store.get('cloud_approval',run['approval_id'])
                        if approval['scope']!=self.cloud_scope(runtime):
                            raise ProviderBlocked('STALE_CLOUD_AUTHORIZATION')
                        agent.provider=OpenAIProvider(self.spend,session=conversation_id,scope=approval['scope'],
                            approval=approval['ledger_id'],run=run_id,reasoning=run['reasoning'],cancelled=flag.is_set,policy_check=self.check_cloud_privacy)
                    self.guard_agent(agent, runtime)
                    runtime.workspace.on_record = audit
                    result = agent.ask(question)
                with self.lock, self.store.lock:
                    status = 'cancelled' if flag.is_set() else {'answered':'completed', 'blocked':'blocked', 'incomplete':'incomplete'}.get(result.get('status'), 'failed')
                    if flag.is_set():
                        result = {**result, 'status':'cancelled', 'answer':'Run cancelled. Tools that already finished remain in the activity log; no further tools were started.'}
                    self.store.put('message', conversation_id, role='assistant', content=result.get('answer',''), status=status, run_id=run_id)
                    self.store.patch('run', run_id, status=status, result=clean(result))
                    emit('run_'+status, {'result':result})
        except Exception:
            # Avoid credential-bearing exception strings and stack traces in the HTTP/log boundary.
            result = {'status':'failed','answer':'The local run failed. No completed analysis is claimed. Check the recorded tool events and try a new analysis.'}
            with self.store.lock:
                self.store.patch('run', run_id, status='failed', result=result)
                self.store.put('message', conversation_id, role='assistant', content=result['answer'], status='failed', run_id=run_id)
                emit('run_failed', {'result':result})
        finally:
            runtime.refresh_snapshot()
            runtime.workspace.on_record = None
            agent = runtime.agents.get(conversation_id)
            if agent:
                agent.on_event = agent.should_cancel = None
            self.cancellations.pop(run_id, None)

    def capture_artifacts(self, runtime, run_id, result):
        if result.get('status') != 'exported':
            return
        query_id = result['query_id']
        for fmt in ('csv','excel','pdf'):
            output = result.get(fmt)
            if not output:
                continue
            path = Path(output if isinstance(output,str) else output['path']).resolve()
            if not path.is_relative_to(runtime.root/'outputs') or not path.is_file():
                raise ValueError('Export is outside workspace outputs')
            self.store.put('artifact', runtime.key, workspace_id=runtime.key, run_id=run_id,
                           query_id=query_id, format=fmt, name=path.name, path=str(path),
                           sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size)

    def close(self):
        for flag in list(self.cancellations.values()):
            flag.set()
        self.executor.shutdown(wait=True, cancel_futures=False)
        for runtime in self.runtimes.values():
            runtime.workspace.db.close()
        self.store.db.close()
        self.spend.db.close()


def create_app(data_root=None, import_roots=None, agent_factory=AnalystAgent):
    manager = Manager(data_root or PROJECT_ROOT/'.analyst_web', import_roots if import_roots is not None else [PROJECT_ROOT/'data',PROJECT_ROOT/'examples/collections'], agent_factory)
    cookie_secret, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    @asynccontextmanager
    async def lifespan(app):
        yield
        manager.close()
    app = FastAPI(title='Gridline Local', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.manager = manager

    @app.middleware('http')
    async def boundary(request, call_next):
        if request.url.hostname not in {'localhost','127.0.0.1'}:
            return JSONResponse({'detail':'Untrusted host'},403)
        origin = request.headers.get('origin')
        if (origin and origin not in ORIGINS) or request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'Untrusted browser origin'},403)
        if request.url.path not in {'/api/bootstrap','/api/health'}:
            if not secrets.compare_digest(request.cookies.get('gridline_session',''), cookie_secret):
                return JSONResponse({'detail':'Open the local app to establish a session'},401)
            if request.method not in {'GET','HEAD','OPTIONS'} and not secrets.compare_digest(request.headers.get('x-csrf-token',''),csrf):
                return JSONResponse({'detail':'Invalid local request token'},403)
        response = await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='no-referrer'
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'detail':'Request validation failed', 'fields':[str(e['loc'][-1]) for e in exc.errors()]},422)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'detail':clean(str(exc))},400)

    @app.get('/api/health')
    def health(): return {'status':'ok','app':'gridline-local'}

    @app.get('/api/bootstrap')
    def bootstrap():
        response=JSONResponse(clean({'csrf_token':csrf,'workspaces':manager.store.list('workspace'),
            'providers':manager.models(),'settings':manager.store.list('settings'),
            'import_roots':[str(p) for p in manager.import_roots]}))
        response.set_cookie('gridline_session',cookie_secret,httponly=True,samesite='strict',path='/api')
        return response

    @app.get('/api/providers')
    def providers(): return manager.models()

    @app.post('/api/workspaces',status_code=201)
    def create_workspace(body:NameInput):
        return manager.store.put('workspace',name=body.name.strip() or 'Workspace')

    @app.get('/api/workspaces')
    def workspaces(): return manager.store.list('workspace')

    @app.get('/api/workspaces/{key}')
    def workspace(key:str):
        record=manager.store.get('workspace',key);runtime=manager.runtime(key)
        return clean({**record,'tables':runtime.tables(),'files':[{k:v for k,v in f.items() if k!='path'} for f in manager.store.list('file',key)],
                'conversations':manager.store.list('conversation',key),'active_run':manager.active(key),
                'recovery':{'derived_state_restored':False,'message':'Raw sources reload on restart. Saved conversations are context, not restored derived calculations. Recompute derived results.','errors':runtime.recovery_errors}})

    @app.post('/api/workspaces/{key}/load')
    def load(key:str,body:PathInput): return manager.source(manager.runtime(key),body.path,body.namespace)

    @app.post('/api/workspaces/{key}/upload')
    async def upload(key:str,file:UploadFile=File(...),reviewed_plan:bool=Form(False)):
        runtime=manager.runtime(key);name=file.filename or ''
        if not name or '/' in name or '\\' in name or name.startswith('.') or Path(name).suffix.lower() not in ({'.json'} if reviewed_plan else {'.csv','.xlsx'}):
            raise HTTPException(400,'Choose a CSV/XLSX source, or an explicitly reviewed JSON plan')
        if manager.active(key): raise HTTPException(409,'Wait for the active run before loading files')
        directory=runtime.root/'uploads'/identifier();directory.mkdir(parents=True)
        path=directory/name;size=0
        try:
            with path.open('xb') as output:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>MAX_FILE:raise HTTPException(413,'Upload exceeds the 50 MB limit')
                    output.write(chunk)
            if reviewed_plan:
                with runtime.ready() as w:
                    plan_id=w.register_plan(manager.allowed_path(runtime,path,('.json',)))
                    return {'plan_id':plan_id,'status':'registered_reviewed_plan'}
            return await run_in_threadpool(manager.source,runtime,path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get('/api/workspaces/{key}/tables/{table_id}')
    def table(key:str,table_id:str):
        with manager.runtime(key).ready() as w:
            if table_id not in w.catalog:raise HTTPException(404,'Table not found')
            schema=w.schema(table_id)
            preview=w.query('SELECT * FROM '+quote(table_id),max_rows=20)
            return clean({'schema':schema,'preview':preview})

    @app.post('/api/workspaces/{key}/conversations',status_code=201)
    def conversation(key:str,body:ConversationInput):
        manager.store.get('workspace',key)
        return manager.store.put('conversation',key,workspace_id=key,title=body.title)

    @app.get('/api/conversations/{key}')
    def get_conversation(key:str):
        record=manager.store.get('conversation',key)
        runs=[r for r in manager.store.list('run',record['workspace_id']) if r['conversation_id']==key]
        recovered = bool(manager.store.list('message',key)) and key not in manager.runtime(record['workspace_id']).agents
        return {**record,'messages':manager.store.list('message',key),'runs':runs,
                'events':{r['id']:manager.store.events(r['id']) for r in runs},
                'recovery_notice':'Conversation restored. Raw sources are available; derived calculations need recomputation.' if recovered else ''}

    @app.post('/api/conversations/{key}/cloud-approval')
    def cloud_approval(key:str,body:CloudApprovalInput):
        conversation=manager.store.get('conversation',key)
        runtime=manager.runtime(conversation['workspace_id'])
        if manager.active(runtime.key):raise HTTPException(409,'Wait for the active run before authorizing cloud data')
        manager.check_cloud_privacy(body.confidential_mode)
        if not body.data_authorized or not body.paid_authorized:
            raise HTTPException(403,'Both paid execution and source-derived data exposure require explicit authorization')
        if body.model=='gpt-6.1-sol':
            raise HTTPException(409,'Sol is gated until local and authorized Luna acceptance plus independent case validators are complete. No request was sent.')
        with runtime.ready():
            scope=manager.cloud_scope(runtime)
            try:
                ledger_id=manager.spend.approve(key,scope,body.model,data_authorized=body.data_authorized,
                    paid_authorized=body.paid_authorized,run_cap=body.run_cap,session_cap=body.session_cap,
                    total_cap=body.total_cap,sol_cap=body.sol_cap,max_requests=body.max_requests)
            except ValueError as e:raise HTTPException(400,str(e))
            return manager.store.put('cloud_approval',key,conversation_id=key,scope=scope,model=body.model,
                    ledger_id=ledger_id,run_cap=body.run_cap,session_cap=body.session_cap,total_cap=body.total_cap,
                    disclosure='Prompts, schemas, tool results and necessary excerpts may be sent to OpenAI. Raw files stay local. store=false does not enable Zero Data Retention.')

    @app.get('/api/model-registry')
    def registry():
        from local_analyst.registry import model_registry
        state=manager.models()
        return {'models':model_registry(state['models'],connected=state['available']),
                'cloud_execution':'DISABLED in V3: no paid execution or fallback',
                'profiles':{'general':'PRIMARY: generic tools only','assisted':'Legacy assisted benchmark; separately labelled','reference':'Explicit reviewed deterministic baseline, no model claims'}}

    @app.get('/api/workflows')
    def workflows(workspace_id:str):return manager.store.list('workflow',workspace_id)

    @app.get('/api/workflows/{key}')
    def workflow(key:str):return manager.store.get('workflow',key)

    @app.post('/api/workflows',status_code=202)
    def workflow_start(body:WorkflowInput):
        from local_analyst.v3 import execute
        roles={'planner','analyst','reviewer'} if body.mode=='multi' else {'a','b'} if body.mode=='comparison' else {'analyst'}
        if body.mode!='multi' and body.max_requests>8:raise HTTPException(422,'Single/comparison limits remain eight per competitor')
        if body.role_allocations and (set(body.role_allocations)!=roles or any(v<1 for v in body.role_allocations.values()) or sum(body.role_allocations.values())>body.max_requests):raise HTTPException(422,'Role ceilings must fit one shared request cap')
        if set(body.models)!=roles or any(not m or len(m)>100 for m in body.models.values()):raise HTTPException(422,'Exact role/model assignments required')
        if len({o.id for o in body.objectives})!=len(body.objectives):raise HTTPException(422,'Original objective IDs must be unique')
        runtime=manager.runtime(body.workspace_id)
        owned={f['id']:f for f in manager.store.list('file',runtime.key)}
        if len(set(body.source_ids))!=len(body.source_ids) or any(i not in owned for i in body.source_ids):
            raise HTTPException(403,'Sources must belong to the selected workspace')
        paths=[manager.allowed_path(runtime,owned[i]['path']) for i in body.source_ids]
        if any(fingerprint(p)!=owned[i]['sha256'] for p,i in zip(paths,body.source_ids)):raise HTTPException(409,'Reload changed source versions')
        # No OpenAI provider construction in this controller. Selected cloud roles remain NOT RUN.
        with manager.lock:
            if manager.active(runtime.key):raise HTTPException(409,'Workspace already running')
            record=manager.store.put('workflow',runtime.key,status='running',configuration=body.model_dump(),source_versions={i:owned[i]['sha256'] for i in body.source_ids})
            run=manager.store.put('run',runtime.key,workspace_id=runtime.key,conversation_id='workflow:'+record['id'],model='V3 '+body.mode,status='running')
            record=manager.store.patch('workflow',record['id'],run_id=run['id'])
            flag=threading.Event();manager.cancellations[run['id']]=flag
        def worker():
            try:
                def checkpoint(value):
                    manager.store.patch('workflow',record['id'],progress=value)
                if any(fingerprint(p)!=owned[i]['sha256'] for p,i in zip(paths,body.source_ids)):raise ValueError('SOURCE_VERSION_CHANGED_BEFORE_EXECUTION')
                from local_analyst.contracts import contract_verifier
                result=execute(body.task,paths,manager.root/'workflows'/record['id'],mode=body.mode,models=body.models,verifier=contract_verifier(body.acceptance_contracts) if body.acceptance_contracts else None,
                    financial_contracts=[c for c in body.acceptance_contracts if c.get('kind')=='financial_rowset'],objectives=[o.model_dump() for o in body.objectives],rule_text=body.rule_text,formats=body.formats,profile=body.profile,
                    max_requests=body.max_requests,max_tools=body.max_tools,max_seconds=body.max_seconds,role_allocations=body.role_allocations,cancelled=flag.is_set,on_checkpoint=checkpoint)
                manager.store.patch('workflow',record['id'],status='CANCELLED' if flag.is_set() else result['status'],result=result)
                manager.store.patch('run',run['id'],status='cancelled' if flag.is_set() else 'completed' if result['status']=='COMPLETED' else 'incomplete',result={'status':result['status'],'answer':result.get('answer',{}).get('answer',result.get('reason','Inspect deterministic validation and limitations'))})
            except Exception as e:
                manager.store.patch('workflow',record['id'],status='FAILED',error=type(e).__name__)
                manager.store.patch('run',run['id'],status='failed')
            finally:manager.cancellations.pop(run['id'],None)
        manager.executor.submit(worker)
        return record

    @app.get('/api/workflows/{key}/artifacts/{token}')
    def workflow_artifact(key:str,token:str):
        record=manager.store.get('workflow',key)
        result=record.get('result') or {}
        artifacts=list(result.get('artifacts',[]))
        for child in result.get('runs',[]):artifacts+=child.get('artifacts',[])
        match=next((a for a in artifacts if a.get('sha256')==token),None)
        if not match:raise HTTPException(404,'Artifact not present in frozen execution')
        path=Path(match['path']).resolve();base=(manager.root/'workflows'/key).resolve()
        if not path.is_relative_to(base) or not path.is_file() or fingerprint(path)!=token:raise HTTPException(403,'Artifact boundary/hash check failed')
        return FileResponse(path,filename=path.name)

    @app.get('/api/benchmarks/catalog')
    def benchmark_catalog(workspace_id:str|None=None):
        from local_analyst.benchmarking import TESTS, MODELS
        rules=PROJECT_ROOT/'Clipboard_Health_Case_Study_Rules.md'
        return {'tests':TESTS,'models':MODELS,'rules_name':rules.name if rules.is_file() else None,
            'sol_ready':manager.sol_ready(), 'sources':[{k:v for k,v in f.items() if k!='path'} for f in manager.store.list('file',workspace_id)] if workspace_id else []}

    @app.get('/api/benchmarks')
    def list_benchmarks(workspace_id:str|None=None):
        return manager.store.list('benchmark',workspace_id) if workspace_id else manager.store.list('benchmark')

    @app.post('/api/benchmarks',status_code=202)
    def start_benchmark(body:BenchmarkInput):
        runtime=manager.runtime(body.workspace_id)
        source=manager.store.get('file',body.source_id)
        if source not in manager.store.list('file',runtime.key):raise HTTPException(403,'Source belongs to another workspace')
        path=manager.allowed_path(runtime,source['path'])
        if fingerprint(path)!=source['sha256']:raise HTTPException(409,'Selected source changed after loading; reload before benchmarking')
        if path.suffix.lower()!='.xlsx':raise HTTPException(400,'Full case benchmark requires the original XLSX source')
        rules=PROJECT_ROOT/'Clipboard_Health_Case_Study_Rules.md'
        if not rules.is_file():raise HTTPException(409,'Original case rules are missing')
        if len(set(body.models))!=len(body.models):raise HTTPException(400,'Duplicate model trials are not allowed')
        # Local capability discovery; no OpenAI request. One global worker preserves sequential inference.
        installed={m['id'] for m in manager.models()['models'] if m.get('local',True)}
        if any(m not in CLOUD_MODELS and m not in installed for m in body.models):raise HTTPException(503,'Selected local model is unavailable')
        paid=body.paid_authorized and body.data_authorized
        if body.paid_authorized != body.data_authorized:
            raise HTTPException(403,'Both paid-model and actual-case data exposure authorization are required')
        cloud=[m for m in body.models if m in CLOUD_MODELS]
        if paid and cloud:
            manager.check_cloud_privacy(body.confidential_mode)
            if not os.environ.get('OPENAI_API_KEY'):raise HTTPException(503,'OpenAI credentials are not configured')
            if 'gpt-6.1-sol' in cloud:
                if not manager.sol_ready():raise HTTPException(409,'Sol prerequisites incomplete: current offline checks, local attempts and a real authorized Luna attempt are required')
                if manager.spend.summary()['models']['gpt-6.1-sol']['requests']:
                    raise HTTPException(409,'A Sol attempt already exists. Preserve and diagnose its trace; no automatic repeat is permitted')
                if Decimal(body.sol_cap)>1:raise HTTPException(400,'Separate Sol evaluation cap is $1.00; request a reviewed budget change explicitly')
        with manager.lock:
            if manager.active(runtime.key):raise HTTPException(409,'Workspace already running')
            b=manager.store.put('benchmark',runtime.key,status='running',models=body.models,results=[],source_sha256=fingerprint(path),paid_authorized=paid,data_authorized=body.data_authorized,budgets={'run':body.run_cap,'session':body.session_cap,'application':body.total_cap,'sol':body.sol_cap})
            run=manager.store.put('run',runtime.key,workspace_id=runtime.key,conversation_id='benchmark:'+b['id'],model='sequential benchmark',status='queued')
            b=manager.store.patch('benchmark',b['id'],run_id=run['id'])
            flag=threading.Event();manager.cancellations[run['id']]=flag
        def execute_benchmark():
            from local_analyst.benchmarking import run_case
            results=[]
            try:
                scope=hashlib.sha256(encoded({'source':fingerprint(path),'rules':fingerprint(rules)}).encode()).hexdigest()
                for model in body.models:
                    if flag.is_set():break
                    cloud_provider=None
                    if paid and model in CLOUD_MODELS:
                        ledger_id=manager.spend.approve(b['id'],scope,model,data_authorized=True,paid_authorized=True,
                            run_cap=body.run_cap,session_cap=body.session_cap,total_cap=body.total_cap,sol_cap=body.sol_cap)
                        cloud_provider=OpenAIProvider(manager.spend,session=b['id'],scope=scope,approval=ledger_id,
                            run=b['id']+':'+model,reasoning='low',cancelled=flag.is_set,confidential_mode=body.confidential_mode,policy_check=manager.check_cloud_privacy)
                    r=run_case(model,path,rules,manager.root/'benchmarks'/b['id']/model.replace(':','_').replace('.','_'),provider=cloud_provider,cancelled=flag.is_set,
                        on_event=lambda kind,payload:manager.store.event(run['id'],kind,{**payload,**({'call_id':payload.get('model','')+':'+str(payload['call_id'])} if payload.get('call_id') else {})}))
                    results.append(r);manager.store.patch('benchmark',b['id'],results=results)
                manager.store.patch('benchmark',b['id'],status='cancelled' if flag.is_set() else 'completed',results=results)
                manager.store.patch('run',run['id'],status='cancelled' if flag.is_set() else 'incomplete',result={'status':'incomplete','answer':'Benchmark finished. Inspect individual FAIL/BLOCKED/NOT RUN objectives; no full case pass is claimed.'})
            except Exception as e:
                manager.store.patch('benchmark',b['id'],status='failed',results=results,error=type(e).__name__)
                manager.store.patch('run',run['id'],status='failed')
            finally:
                manager.cancellations.pop(run['id'],None)
        manager.executor.submit(execute_benchmark)
        return b

    @app.get('/api/benchmarks/{key}')
    def get_benchmark(key:str):return manager.store.get('benchmark',key)

    @app.get('/api/benchmarks/{key}/artifacts/{model}/{fmt}')
    def benchmark_artifact(key:str,model:str,fmt:Literal['excel','pdf']):
        b=manager.store.get('benchmark',key)
        r=next((r for r in b.get('results',[]) if r['model']==model),None)
        candidate=(r or {}).get('candidate') or {}
        output=candidate.get('path') if fmt=='excel' else (candidate.get('pdf') or {}).get('path')
        if not output:raise HTTPException(404,'This model did not generate that artifact')
        path=Path(output).resolve()
        if not path.is_relative_to(manager.root/'benchmarks'/key) or not path.is_file():raise HTTPException(403,'Invalid artifact boundary')
        expected=candidate.get('files_sha256',{}).get(path.name)
        if not expected or fingerprint(path)!=expected:raise HTTPException(409,'Artifact hash changed or no verified hash was recorded')
        if any(not Path(p).is_file() or fingerprint(p)!=sha for p,sha in candidate.get('source_sha256',{}).items()):
            raise HTTPException(409,'Benchmark sources changed; recompute in a new run')
        return FileResponse(path,filename=path.name)

    @app.get('/api/benchmarks/{key}/report')
    def benchmark_report(key:str):
        return JSONResponse(manager.store.get('benchmark',key),headers={'Content-Disposition':'attachment; filename="benchmark-report.json"'})

    @app.get('/api/spending')
    def spending():return manager.spend.summary()

    @app.post('/api/conversations/{key}/messages',response_model=RunView,status_code=202)
    def message(key:str,body:MessageInput):return manager.start(key,body)

    @app.get('/api/runs/{key}',response_model=RunView)
    def run(key:str):return manager.store.get('run',key)

    @app.post('/api/runs/{key}/cancel',response_model=RunView)
    def cancel(key:str):
        with manager.lock:
            run=manager.store.get('run',key)
            if run['status'] in TERMINAL:return run
            flag=manager.cancellations.get(key)
            if flag:flag.set()
            manager.store.event(key,'run_cancellation_requested',{'message':'Stopping at the next safe boundary. The current local request/tool may take up to its existing timeout.'})
            return manager.store.patch('run',key,status='cancelling')

    @app.get('/api/runs/{key}/events')
    async def events(key:str,request:Request,after:int=0):
        manager.store.get('run',key)
        try:cursor=max(after,int(request.headers.get('last-event-id','0')))
        except ValueError:raise HTTPException(400,'Invalid event cursor')
        async def stream():
            nonlocal cursor
            while True:
                if await request.is_disconnected():break
                batch=manager.store.events(key,cursor)
                for event in batch:
                    cursor=event['seq']
                    yield f"id: {cursor}\nevent: run_event\ndata: {encoded(event)}\n\n"
                if manager.store.get('run',key)['status'] in TERMINAL and not manager.store.events(key,cursor):break
                if not batch:yield ': waiting\n\n'
                await asyncio.sleep(.15)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    @app.get('/api/workspaces/{key}/evidence/{query_id}')
    def evidence(key:str,query_id:str):
        with manager.runtime(key).ready() as w:return clean(w.query_evidence(query_id))

    @app.get('/api/workspaces/{key}/artifacts')
    def artifacts(key:str):
        manager.store.get('workspace',key)
        return [{k:v for k,v in a.items() if k!='path'} for a in manager.store.list('artifact',key)]

    @app.get('/api/artifacts/{key}/download')
    def download(key:str):
        a=manager.store.get('artifact',key);runtime=manager.runtime(a['workspace_id'])
        with runtime.ready() as w:
            try:w.query_result_frame(a['query_id'])
            except ValueError:raise HTTPException(409,'Artifact evidence is stale or derived state is unavailable. Recompute and export again.')
            path=Path(a['path']).resolve()
            if not path.is_relative_to(runtime.root/'outputs') or not path.is_file():raise HTTPException(404,'Authorized artifact is unavailable')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=a['sha256']:raise HTTPException(409,'Artifact changed since export')
            return FileResponse(path,filename=a['name'],media_type={'csv':'text/csv','excel':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','pdf':'application/pdf'}[a['format']])

    @app.put('/api/settings')
    def settings(body:SettingsInput):
        prior=next((s for s in manager.store.list('settings') if s['id']=='ui'),{})
        merged=SettingsInput(**{**{k:v for k,v in prior.items() if k in SettingsInput.model_fields},**body.model_dump(exclude_unset=True)})
        return manager.store.put('settings',id='ui',**merged.model_dump())

    return app
