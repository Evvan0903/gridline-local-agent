"""Model transports. Cloud is opt-in; no SDK client is created at import time."""
from __future__ import annotations
import copy
import re
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import requests
from typing import Protocol
from decimal import Decimal

CLOUD_MODELS = ('gpt-6-luna', 'gpt-6.1-sol')
PRICING = {
 'version': '2026-10-17-standard-text-v1',
 'sources': ['https://developers.openai.com/api/docs/models/'+m for m in CLOUD_MODELS],
 'models': {'gpt-6-luna': {'input': '0.10', 'cached': '0.01', 'cache_write': '0.125', 'output': '0.50'},
            'gpt-6.1-sol': {'input': '2.00', 'cached': '0.10', 'cache_write': '2.50', 'output': '10.00'}},
}

class ModelProvider(Protocol):
    def complete(self, model, history, tools): ...


class OllamaProvider:
    name='ollama'
    def __init__(self,url,timeout=180):self.url,self.timeout=url,timeout
    def complete(self,model,history,tools):
        response=requests.post(self.url,json={'model':model,'messages':history,'tools':tools,
            'stream':False,'think':False,'options':{'temperature':0,'num_ctx':16384}},timeout=self.timeout)
        response.raise_for_status();body=response.json();message=body['message']
        if not isinstance(message,dict):raise ValueError('Malformed local model message')
        return message, {'usage':{k:body[k] for k in ('prompt_eval_count','eval_count') if isinstance(body.get(k),int)}}


class ProviderBlocked(ValueError):
    pass


def redact(value):
    secrets = [v for k,v in os.environ.items() if v and (k=='OPENAI_API_KEY' or (len(v)>=8 and re.search(r'(?:API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)',k,re.I)))]
    if isinstance(value, str):
        for secret in sorted(secrets,key=len,reverse=True):value=value.replace(secret,'[REDACTED]')
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [redact(v) for v in value]
    return value


def estimated_cost(model, inputs, outputs, cached=0, cache_write=0):
    p = PRICING['models'][model]
    if any(type(v) is not int or v < 0 for v in (inputs, outputs, cached, cache_write)) or cached+cache_write > inputs:
        raise ProviderBlocked('INVALID_USAGE')
    # Long context is deliberately rejected by the application; only Standard text is enabled.
    if inputs > 200000:
        raise ProviderBlocked('CONTEXT_LIMIT')
    return (Decimal(inputs-cached-cache_write)*Decimal(p['input']) + Decimal(cached)*Decimal(p['cached']) +
            Decimal(cache_write)*Decimal(p['cache_write']) + Decimal(outputs)*Decimal(p['output']))/1000000


class SpendLedger:
    """Atomic persistent reservations survive crashes. Unknown charges retain their full reserve.

    `observed_estimate_usd` is usage-priced, not a provider invoice. Failed/unknown
    requests are never assumed free. No automatic retries and no automatic escalation.
    """
    def __init__(self, path):
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,check_same_thread=False)
        path.chmod(0o600)
        self.lock=threading.RLock()
        self.db.executescript('''CREATE TABLE IF NOT EXISTS approvals(
          id TEXT PRIMARY KEY, session TEXT, scope TEXT, model TEXT, data INTEGER,
          run_cap TEXT, session_cap TEXT, total_cap TEXT, sol_cap TEXT, max_requests INTEGER);
          CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, approval TEXT, session TEXT,
          run TEXT, model TEXT, reserve TEXT, charge TEXT, status TEXT, usage TEXT, created REAL);''')
        columns={r[1] for r in self.db.execute('PRAGMA table_info(requests)')}
        for column in ('request_json','response_json','error_json'):
            if column not in columns:self.db.execute('ALTER TABLE requests ADD COLUMN '+column+' TEXT')
        self.db.commit()

    def transport_trace(self,key,*,request=None,response=None,error=None):
        with self.lock,self.db:
            for column,value in (('request_json',request),('response_json',response),('error_json',error)):
                if value is not None:self.db.execute('UPDATE requests SET '+column+'=? WHERE id=?',
                    (json.dumps(redact(value),ensure_ascii=False,default=str),key))

    def approve(self, session, scope, model, *, data_authorized, paid_authorized,
                run_cap='0.25', session_cap='2.00', total_cap='2.00', sol_cap='1.00', max_requests=8):
        try:caps=[Decimal(str(v)) for v in (run_cap,session_cap,total_cap,sol_cap)]
        except ArithmeticError:raise ProviderBlocked('INVALID_BUDGET') from None
        if not paid_authorized or model not in CLOUD_MODELS or not scope:
            raise ProviderBlocked('CLOUD_AUTHORIZATION_REQUIRED')
        if any(not c.is_finite() or c<=0 or c>100 for c in caps) or not 1<=max_requests<=8:
            raise ProviderBlocked('INVALID_BUDGET')
        key=uuid.uuid4().hex
        with self.lock,self.db:
            self.db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?)',
               (key,session,scope,model,int(data_authorized),*[str(c) for c in caps],max_requests))
        return key

    def reserve(self, approval, session, scope, run, model, amount):
        with self.lock,self.db:
            self.db.execute('BEGIN IMMEDIATE')
            a=self.db.execute('SELECT * FROM approvals WHERE id=?',(approval,)).fetchone()
            if not a or (a[1],a[2],a[3]) != (session,scope,model) or not a[4]:
                raise ProviderBlocked('CLOUD_DATA_AND_PAID_AUTHORIZATION_REQUIRED')
            rows=self.db.execute('SELECT run,model,reserve,charge,status,session FROM requests').fetchall()
            charged=lambda r: Decimal(r[3] if r[3] is not None else r[2])
            checks=[(sum((charged(r) for r in rows if r[0]==run),Decimal(0)),a[5]),
                    (sum((charged(r) for r in rows if r[5]==session),Decimal(0)),a[6]),
                    (sum((charged(r) for r in rows),Decimal(0)),a[7])]
            if model=='gpt-6.1-sol':
                checks.append((sum((charged(r) for r in rows if r[1]==model),Decimal(0)),a[8]))
            if any(used+amount>Decimal(cap) for used,cap in checks):
                raise ProviderBlocked('BUDGET_EXCEEDED')
            if sum(r[0]==run for r in rows)>=a[9]:
                raise ProviderBlocked('REQUEST_LIMIT_EXCEEDED')
            if any(r[4] in {'unknown','reserved'} for r in rows):
                raise ProviderBlocked('UNRECONCILED_REQUEST_REQUIRES_REVIEW')
            key=uuid.uuid4().hex
            self.db.execute('INSERT INTO requests(id,approval,session,run,model,reserve,charge,status,usage,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (key,approval,session,run,model,str(amount),None,'reserved',None,time.time()))
        return key

    def finish(self, key, usage=None, *, failed=False):
        with self.lock,self.db:
            model=self.db.execute('SELECT model FROM requests WHERE id=?',(key,)).fetchone()[0]
            charge=None
            if usage is not None:
                d=usage.get('input_tokens_details') or {}
                charge=str(estimated_cost(model,usage['input_tokens'],usage['output_tokens'],
                    d.get('cached_tokens',0),d.get('cache_write_tokens',0)))
            self.db.execute('UPDATE requests SET charge=?,status=?,usage=? WHERE id=?',
                (charge,'unknown' if charge is None else ('failed_with_usage' if failed else 'observed'),json.dumps(usage),key))
        return self.summary()

    def summary(self, run=None):
        with self.lock:
            rows=self.db.execute('SELECT model,reserve,charge,status,usage FROM requests'+(' WHERE run=?' if run else ''),(run,) if run else ()).fetchall()
        result={}
        for model in CLOUD_MODELS:
            selected=[r for r in rows if r[0]==model]
            usage=[json.loads(r[4]) for r in selected if r[4] and r[4]!='null']
            result[model]={'requests':len(selected),'input_tokens':sum(u['input_tokens'] for u in usage),
               'output_tokens':sum(u['output_tokens'] for u in usage),
               'reasoning_tokens':sum((u.get('output_tokens_details') or {}).get('reasoning_tokens',0) for u in usage),
               'cached_tokens':sum((u.get('input_tokens_details') or {}).get('cached_tokens',0) for u in usage),
               'cache_write_tokens':sum((u.get('input_tokens_details') or {}).get('cache_write_tokens',0) for u in usage),
               'observed_estimate_usd':str(sum((Decimal(r[2]) for r in selected if r[2] is not None),Decimal(0))),
               'committed_usd':str(sum((Decimal(r[2] if r[2] is not None else r[1]) for r in selected),Decimal(0))),
               'unknown_usage_requests':sum(r[2] is None for r in selected)}
        return {'pricing_version':PRICING['version'],'models':result,'invoice_cost_usd':None}


def response_tools(tools):
    """Responses flattened function shape, explicitly non-strict for open parameter maps.

    Arbitrary SQL parameter/override keys cannot be represented by strict closed objects.
    Existing tools validate arguments locally; never silently normalize optional fields.
    """
    return [{'type':'function',**copy.deepcopy(t['function']),'strict':False} for t in tools]


class OpenAIProvider:
    name='openai'
    def __init__(self, ledger, *, session, scope, approval, run, reasoning='low', client=None,
                 max_output_tokens=4096, cancelled=None, confidential_mode=False, policy_check=None):
        if reasoning not in ('low','medium','high'):
            raise ProviderBlocked('UNSUPPORTED_REASONING_EFFORT')
        self.ledger,self.session,self.scope,self.approval,self.run=ledger,session,scope,approval,run
        self.reasoning,self.client,self.max_output_tokens,self.cancelled=reasoning,client,max_output_tokens,cancelled
        self.confidential_mode,self.policy_check=confidential_mode,policy_check
        if type(max_output_tokens) is not int or not 256<=max_output_tokens<=8192:
            raise ProviderBlocked('INVALID_OUTPUT_LIMIT')

    def complete(self, model, history, tools):
        if self.confidential_mode:
            raise ProviderBlocked('CONFIDENTIAL_MODE_UNAVAILABLE: strict minimization is incomplete; cloud requests are blocked')
        if self.policy_check:self.policy_check()
        if model not in CLOUD_MODELS:
            raise ProviderBlocked('MODEL_NOT_APPROVED')
        if self.cancelled and self.cancelled():
            raise ProviderBlocked('CANCELLED')
        inputs=[]
        for m in history:
            if m.get('_responses_output') is not None:
                inputs.extend(copy.deepcopy(m['_responses_output']))
                if m.get('_validation_result'):
                    inputs.append({'role':'developer','content':'Application completion validation of the preceding response: '+json.dumps(m['_validation_result'])})
            elif m['role']=='tool':
                if not m.get('tool_call_id'):
                    raise ProviderBlocked('MISSING_CALL_ID')
                inputs.append({'type':'function_call_output','call_id':m['tool_call_id'],'output':m['content']})
            else:
                inputs.append({'role':'developer' if m['role']=='system' else m['role'],'content':m.get('content') or ''})
        inputs=redact(inputs)
        converted=redact(response_tools(tools))
        # UTF-8 bytes + protocol allowance is a conservative upper bound, not claimed token usage.
        bound=len(json.dumps([inputs,converted],ensure_ascii=False).encode())+8192
        reserve=estimated_cost(model,bound,self.max_output_tokens,cache_write=bound)
        if self.client is None:
            if not os.environ.get('OPENAI_API_KEY'):
                raise ProviderBlocked('CREDENTIALS_MISSING')
            from openai import OpenAI
            self.client=OpenAI(api_key=os.environ['OPENAI_API_KEY'],base_url='https://api.openai.com/v1',
                               max_retries=0,timeout=90)
        key=self.ledger.reserve(self.approval,self.session,self.scope,self.run,model,reserve)
        try:
            arguments=dict(model=model,input=inputs,tools=converted,store=False,
                include=['reasoning.encrypted_content'],reasoning={'effort':self.reasoning},
                max_output_tokens=self.max_output_tokens,service_tier='default',parallel_tool_calls=False)
            self.ledger.transport_trace(key,request=arguments)
            response=self.client.responses.create(**arguments)
            raw=response.model_dump(mode='json',exclude_none=True)
            self.ledger.transport_trace(key,response=raw)
            summary=self.ledger.finish(key,raw.get('usage'),failed=raw.get('status')!='completed')
        except Exception as error:
            self.ledger.transport_trace(key,error={'class':type(error).__name__,'status_code':getattr(error,'status_code',None),'body':getattr(error,'body',None)})
            self.ledger.finish(key)
            # Provider exception bodies can echo headers or input. Keep only exception class.
            raise ProviderBlocked('OPENAI_REQUEST_FAILED:'+type(error).__name__) from None
        if raw.get('status')!='completed':
            raise ProviderBlocked('OPENAI_RESPONSE_INCOMPLETE')
        outputs=raw.get('output',[])
        message={'role':'assistant','content':getattr(response,'output_text','') or '',
                 '_responses_output':outputs,'tool_calls':[
                     {'id':o['call_id'],'function':{'name':o['name'],'arguments':o['arguments']}}
                     for o in outputs if o.get('type')=='function_call']}
        return message, {'usage':raw.get('usage'), 'spend':summary,'provider':'openai','request_id':key}


def sol_readiness():
    """Fail closed until current offline checks and a real authorized Luna case attempt exist."""
    from .config import PROJECT_ROOT
    path=PROJECT_ROOT/'reports/validation/gridline_v2/readiness.json'
    try:
        record=json.loads(path.read_text())
        import hashlib
        current={str(p.relative_to(PROJECT_ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                 for folder in ('local_analyst','examples/clipboard') for p in (PROJECT_ROOT/folder).glob('*.py')}
        return record.get('offline_pipeline')=='PASS' and record.get('engine_sha256')==current
    except (OSError,ValueError):return False
