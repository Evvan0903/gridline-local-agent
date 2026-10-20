"""Local-first typed workflows. Cloud dispatch is deliberately disabled in V3.

Isolated independent runs; bounded Planner -> Analyst -> Reviewer handoffs;
model confidence cannot override deterministic verification.
"""
from __future__ import annotations
from dataclasses import dataclass,field,asdict
from pathlib import Path
import json,time,hashlib,re,copy
from typing import Callable
from .workspace import Workspace,dump,fingerprint
from .agent import AnalystAgent,tool,TEXT
from .providers import OllamaProvider,ProviderBlocked,CLOUD_MODELS,redact
from .financial_model import evaluate,export_workspace_model
from .exporting import export_pdf
from .financial_schema import FINANCIAL_SPEC,conditional_spec
from .financial_validation import FinancialPlanError,validate_financial_spec
from .financial_eligibility import validate_financial_rows
from .reliability import RootCauseProgress,compact_history

STATES={'NOT_STARTED','IN_PROGRESS','COMPLETED','BLOCKED','FAILED'}
PROFILES=('general','assisted','reference')
EXTENDED_LOCAL='extended_local_60m'
GENERIC={'inspect_schema','query_sql','check_join','check_quality','discover_relationships','reconcile_queries','inspect_query_evidence','build_financial_model','export_result'}

@dataclass
class Objective:
    id:str
    requirement:str
    kind:str='financial'
    dependencies:list[str]=field(default_factory=list)
    required_evidence:list[str]=field(default_factory=list)
    required_artifacts:list[str]=field(default_factory=list)
    status:str='NOT_STARTED'
    evidence_refs:list[str]=field(default_factory=list)
    validation_results:list=field(default_factory=list)
    missing_information:list[str]=field(default_factory=list)
    blocking_reason:str|None=None

@dataclass
class Budget:
    max_requests:int|None=8
    max_tools:int|None=24
    max_seconds:int=600
    request_timeout:int=180
    extended:bool=False
    stop_reason:str|None=None
    requests:int=0
    tools:int=0
    start:float=field(default_factory=time.monotonic)
    cancelled:Callable=field(default=lambda:False,repr=False)
    def check(self):
        if self.stop_reason:raise ProviderBlocked(self.stop_reason)
        if self.cancelled():raise ProviderBlocked('CANCELLED')
        if time.monotonic()-self.start>=self.max_seconds:raise ProviderBlocked('WALL_TIME_LIMIT')
    def request(self):
        self.check()
        if self.max_requests is not None and self.requests>=self.max_requests:raise ProviderBlocked('SHARED_REQUEST_BUDGET')
        self.requests+=1
    def call(self):
        self.check()
        if self.max_tools is not None and self.tools>=self.max_tools:raise ProviderBlocked('SHARED_TOOL_BUDGET')
        self.tools+=1
    def snapshot(self):
        return {'requests':self.requests,'tools':self.tools,'remaining_requests':None if self.max_requests is None else self.max_requests-self.requests,
            'remaining_tools':None if self.max_tools is None else self.max_tools-self.tools,'remaining_seconds':max(0,round(self.max_seconds-(time.monotonic()-self.start),2)),
            'request_timeout_seconds':self.request_timeout,'extended_local':self.extended,'cloud_requests':0,'cloud_cost_usd':'0'}

class Trace(list):
    def __init__(self,path,checkpoint=None):super().__init__();self.path=path;self.checkpoint=checkpoint
    def append(self,event):
        event=redact(event);super().append(event)
        with self.path.open('a') as f:f.write(dump(event)+'\n')
        if self.checkpoint:self.checkpoint({'status':'running','last_event':{k:event.get(k) for k in ('role','model','kind','budget')}})

class BoundedProvider:
    def __init__(self,inner,budget,role,events,allocation):
        self.inner,self.budget,self.role,self.events,self.allocation=inner,budget,role,events,allocation
        self.calls=0
    def complete(self,model,history,tools):
        if model in CLOUD_MODELS:raise ProviderBlocked('V3_CLOUD_DISABLED')
        if self.allocation is not None and self.calls>=self.allocation:raise ProviderBlocked('ROLE_REQUEST_BUDGET')
        self.budget.request();self.calls+=1
        # Reserve the last role request for synthesis; no hidden extra model call.
        remaining=max(0,self.budget.max_seconds-(time.monotonic()-self.budget.start))
        last=(self.allocation is not None and self.calls==self.allocation) or (self.budget.max_tools is not None and self.budget.tools>=self.budget.max_tools) or remaining<=(2*self.budget.request_timeout if self.budget.extended else 180)
        if isinstance(self.inner,OllamaProvider):self.inner.timeout=max(.1,min(self.budget.request_timeout,remaining))
        self.final_synthesis=last
        h,reused=compact_history(history)
        h.append({'role':'system','content':dump({'controller_budget':self.budget.snapshot(),'final_synthesis_required':last,'synthesis_reserve_seconds':self.budget.request_timeout if self.budget.extended else 180,
           'instruction':'Final available role request: no tools, summarize only completed evidence and explicitly missing objectives.' if last else 'Use batched independent tools; reuse existing schemas. Dependent query IDs require a later turn.'})})
        h=redact(h);selected=redact([] if last else tools)
        self.events.append({'role':self.role,'model':model,'kind':'model_request','history':h,'tools':selected,'reused_tool_payloads':reused,'final_synthesis_required':last,'budget':self.budget.snapshot()})
        try:msg,meta=self.inner.complete(model,h,selected)
        except Exception as error:
            self.events.append({'kind':'model_error','final_synthesis_required':last,'role':self.role,'model':model,'category':type(error).__name__,'error':str(error),'budget':self.budget.snapshot()})
            raise
        self.budget.check()
        self.events.append({'role':self.role,'model':model,'kind':'model_response','final_synthesis_required':last,'usage':meta.get('usage'),
            'message':redact(msg),'budget':self.budget.snapshot()})
        if not msg.get('tool_calls') and re.match(r'^\s*<tool_call>',str(msg.get('content',''))):raise ProviderBlocked('UNPARSED_TOOL_CALL_MARKUP: no text command was executed')
        if last and msg.get('tool_calls'):raise ProviderBlocked('FINAL_SYNTHESIS_MISSING')
        return msg,meta

def sql_check(workspace,sql,parameters=None):
    """AST read-only/file guard followed by DuckDB binding, not row execution."""
    try:
        tables,warnings=workspace.validate_sql(sql)
        workspace.db.execute('EXPLAIN '+sql,parameters or {}).fetchall()
        return {'status':'validated','error_category':None,'tables':tables,'warnings':warnings,
            'dataset_versions':{t:workspace.catalog[t]['version_id'] for t in tables},
            'limitations':['Syntax/types/binding are not financial meaning. Join cardinality warnings require review.']}
    except Exception as error:
        return {'status':'error','error_category':'SQL_BINDING_OR_SECURITY','error':str(error),
            'available_columns':{t:list(f.columns) for t,f in workspace.frames.items()},'limitations':['No data query executed']}

def _json(text):
    text=str(text).strip();text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
    try:return json.loads(text)
    except ValueError:return {'status':'invalid_handoff','raw':text[:10000]}

def handoff(role,value,objective_ids):
    if not isinstance(value,dict):return {'status':'invalid_handoff','errors':['JSON object required']}
    if role=='planner':
        items=value.get('objectives',[])
        if not isinstance(items,list) or any(not isinstance(x,dict) for x in items) or {x.get('id') for x in items}!=set(objective_ids):
            return {'status':'invalid_handoff','errors':['Every original objective ID must appear in planner objectives'],'proposal':value}
        if any(not isinstance(x.get('steps'),list) or not x['steps'] for x in items):
            return {'status':'invalid_handoff','errors':['Nonempty typed steps required'],'proposal':value}
        return value
    status=str(value.get('status','')).upper()
    status={'PASS':'COMPLETED','COMPLETE':'COMPLETED','FAIL':'FAILED','INCOMPLETE':'BLOCKED'}.get(status,status)
    if status not in {'COMPLETED','PARTIAL','FAILED','BLOCKED'} or not isinstance(value.get('findings'),list):
        return {'status':'invalid_handoff','errors':['Reviewer needs status and findings list'],'proposal':value}
    return {**value,'status':status,'missing_work':value.get('missing_work',[])}

def configure_agent(w,model,provider,budget,objectives,events,formats,profile='general',approved_rule_text='',financial_contracts=()):
    agent=AnalystAgent(w,model=model,provider=provider,max_steps=provider.allocation,should_cancel=budget.cancelled)
    if budget.extended:
        agent.tool_call_limit=agent.batch_call_limit=None
        agent.extended_completion_repair=True
    agent.functions={k:v for k,v in agent.functions.items() if k in GENERIC}
    agent.tools=[t for t in agent.tools if t['function']['name'] in agent.functions]
    proposals=[]
    def plan(items):
        if len(items)>20:raise ValueError('At most 20 objectives')
        ids={o.id for o in objectives}
        if any(not isinstance(x,dict) for x in items) or {x.get('id') for x in items}!=ids:raise ValueError('Plan must cover every original objective ID: '+', '.join(sorted(ids)))
        # Proposed steps do not replace or delete original controller requirements.
        proposals.append(items)
        return {'status':'proposed','plan':items,'controller_objectives':[asdict(x) for x in objectives],
            'evidence_id':w.record('task_plan',items=items),'limitations':['Plan is not executed evidence or approved rules']}
    contracts={c['id']:copy.deepcopy(c) for c in financial_contracts}
    def eligibility(query_id,contract_id=None,frame=None,totals=None,rows_only=False):
        if contract_id is None and len(contracts)==1:contract_id=next(iter(contracts))
        return validate_financial_rows(w,query_id,contracts.get(contract_id),result_frame=frame,totals=totals,rowset_only=rows_only)
    def require_eligibility(query_id,frame=None,totals=None,rows_only=False):
        if not contracts:return {'status':'BLOCKED','reason':'No reviewed eligibility contract; conditional proposal only'}
        if len(contracts)!=1:raise ValueError('Output requires one unambiguous reviewed eligibility contract; split scoped workflows')
        result=eligibility(query_id,frame=frame,totals=totals,rows_only=rows_only)
        if result['status']!='PASS':raise FinancialPlanError([{'error_code':result.get('error_code') or 'ELIGIBILITY_FAILED','json_path':result.get('json_path') or '$.financial_contract','message':result.get('reason') or str(result.get('issues')),'expected_type':'eligible payable rows and currency-safe totals','received_type':'query rows','semantic_validation':result}])
        return result
    def define(spec,query_id):
        if 'excel' not in formats:raise ValueError('Excel export authorization required')
        if not approved_rule_text:raise ValueError('No explicit governing rule text; cannot silently approve model assumptions')
        evidence=w.query_evidence(query_id)
        if not evidence.get('dependencies'):raise ValueError('Source-backed input query required')
        frame=w.query_result_frame(query_id)
        validate_financial_spec(spec,frame);spec=conditional_spec(spec)
        require_eligibility(query_id,frame,rows_only=True)
        evaluated=evaluate(spec,frame)
        semantic=require_eligibility(query_id,evaluated,spec.get('totals',[]))
        name='proposal_'+hashlib.sha256(dump(spec).encode()).hexdigest()[:12]
        path=w.root/(name+'.json')
        path.write_text(dump({'plan_id':name,'financial_model':spec,'rule_text_sha256':hashlib.sha256(approved_rule_text.encode()).hexdigest(),
             'status':'MODEL_PROPOSED_CONDITIONAL_NOT_BUSINESS_APPROVAL'}))
        pid=w.register_plan(path)
        return {'status':'conditional_proposal','semantic_validation':semantic,'plan_id':pid,'query_id':query_id,'evidence_id':w.record('financial_plan_proposal',plan_id=pid,query_id=query_id),
            'limitations':['Expression safety checked; business interpretation must pass independent verification. Not approved payroll.']}
    def report(query_id,title,paragraphs):
        if 'pdf' not in formats:raise ValueError('Explicit PDF export authorization required')
        evidence=w.query_evidence(query_id)
        if not evidence.get('dependencies'):raise ValueError('Source-backed query required')
        if len(paragraphs)>12 or any(len(p)>1800 for p in paragraphs):raise ValueError('Report text bounds')
        dest=w.root/'outputs'/'analyst_report.pdf'
        if dest.exists():raise ValueError('Report destination already exists')
        frame=w.query_result_frame(query_id)
        require_eligibility(query_id)
        export_pdf(title,[{'title':'Findings (model authored; semantic verification separate)','paragraphs':paragraphs,
            'rows':[list(frame.columns)]+frame.head(20).astype(str).values.tolist()}],dest)
        from pypdf import PdfReader
        pages=len(PdfReader(dest).pages)
        return {'status':'exported','pdf':{'path':str(dest)},'query_id':query_id,'pages':pages,
            'evidence_id':w.record('report_export',query_id=query_id,path=str(dest)),'limitations':['PDF snapshot; prose is model-authored, not independently verified']}
    def objective_result(objective_id,evidence_refs,status='COMPLETED',blocking_reason=None):
        obj=next((x for x in objectives if x.id==objective_id),None)
        if obj is None:raise ValueError('Unknown original objective ID')
        if status not in STATES:raise ValueError('Invalid objective state')
        known={e['event_id'] for e in (json.loads(line) for line in w.audit_path.read_text().splitlines())} if w.audit_path.exists() else set()
        known.update(w.queries);known.update(w.catalog)
        if set(evidence_refs)-known:raise ValueError('Unknown evidence reference; use actual tool/query IDs')
        obj.status='IN_PROGRESS' if status=='COMPLETED' else status
        obj.evidence_refs=list(evidence_refs);obj.blocking_reason=blocking_reason
        return {'status':'recorded_pending_validation','objective':asdict(obj),'limitations':['Model cannot mark deterministic acceptance PASS']}
    def workbook(sha256,parameter_changes):
        from .verification import recalculate_workbook
        candidates=[p for p in (w.root/'outputs').rglob('*.xlsx') if fingerprint(p)==sha256]
        if len(candidates)!=1:raise ValueError('Unknown or ambiguous artifact hash')
        result=recalculate_workbook(candidates[0],w.root/'validations'/('check_'+str(budget.tools)+'.xlsx'),parameter_changes)
        result['evidence_id']=w.record('workbook_verification',result=result)
        return result
    funcs={'validate_financial_rows':lambda query_id,contract_id=None:eligibility(query_id,contract_id),'validate_workbook':workbook,'set_task_plan':plan,'define_financial_plan':define,'export_report':report,'record_objective':objective_result,
        'validate_sql':lambda sql,parameters=None:sql_check(w,sql,parameters)}
    schemas=[tool('validate_financial_rows','Validate payable-only source coverage, held/disputed/unknown audit and currency-safe totals using a supplied reviewed contract ID. Unknown rules BLOCKED. Does not verify formula amounts.',{'query_id':TEXT,'contract_id':TEXT},['query_id']),tool('validate_workbook','Reopen/recalculate a generated workbook identified by its exact SHA256, changing only finite numeric parameter cells in a validation COPY. Does not approve business rules.',{'sha256':TEXT,'parameter_changes':{'type':'object'}},['sha256','parameter_changes']),tool('set_task_plan','Propose structured steps tied to every original objective. No computations/approval claimed.',{'items':{'type':'array','items':{'type':'object'}}},['items']),
        tool('define_financial_plan','Propose safe financial_model spec tied to governing text and current query; not business approval. Required keys: version (integer 1), rule_ref (reference supplied governing text), source_key, parameters={name:{value,description}}, ordered calculations=[{id,expression}], totals=[calculation id]. Expressions: {field:name},{param:name},{mul:[a,b]},{add:[a,b]},{sub:[a,b]},{round:[value,2]}, etc. Do not duplicate input column names as calculation IDs. Bare string names are invalid references. Correct: {"mul":[{"field":"net_amount"},{"param":"rate"}]}. Return plan_id; build in later call.',{'spec':copy.deepcopy(FINANCIAL_SPEC),'query_id':TEXT},['spec','query_id']),
        tool('export_report','Create a PDF snapshot only when explicitly requested, grounded in current query. No files outside workspace.',{'query_id':TEXT,'title':TEXT,'paragraphs':{'type':'array','items':TEXT}},['query_id','title','paragraphs']),
        tool('record_objective','Record evidence or blocking reason for an original objective. Completion remains pending deterministic verification.',{'objective_id':TEXT,'evidence_refs':{'type':'array','items':TEXT},'status':TEXT,'blocking_reason':TEXT},['objective_id','evidence_refs']),
        tool('validate_sql','Validate read-only SQL AST, columns/aliases/types with local EXPLAIN before executing. Not semantic proof.',{'sql':TEXT,'parameters':{'type':'object'}},['sql'])]
    agent.functions.update(funcs);agent.tools+=schemas
    original_query=agent.functions['query_sql']
    def checked_query(sql,parameters=None,max_rows=30):
        check=sql_check(w,sql,parameters)
        if check['status']=='error':return check
        return original_query(sql,parameters,max_rows)
    agent.functions['query_sql']=checked_query
    # Recheck output paths against the same explicit contract; model cannot skip it.
    for output_tool in ('build_financial_model','export_result'):
        original=agent.functions[output_tool]
        def eligible_output(_fn=original,_name=output_tool,**args):
            if contracts:
                frame=None;totals=None
                if _name=='build_financial_model':
                    spec=w.reviewed_plan(args['plan_id'])['financial_model']
                    require_eligibility(args['query_id'],rows_only=True)
                    frame=evaluate(spec,w.query_result_frame(args['query_id']));totals=spec.get('totals',[])
                require_eligibility(args['query_id'],frame,totals)
            return _fn(**args)
        agent.functions[output_tool]=eligible_output
    cache={}
    progress=RootCauseProgress()
    def schema(table_id=None):
        if table_id is None:w.dependencies(w.catalog)
        elif table_id in w.catalog:w.dependencies([table_id])
        key=(table_id,tuple((t,m['version_id']) for t,m in w.catalog.items()))
        hit=key in cache
        if not hit:cache[key]=w.schema(table_id)
        return {**copy.deepcopy(cache[key]),'cached_evidence':hit,'limitations':['Reuse current schema; no new data computation']}
    agent.functions['inspect_schema']=schema
    # Every generic tool, including failed calls, consumes the same shared budget.
    def observe(name,args,result):
        reason=progress.observe(name,args,result,turn=provider.calls)
        if reason:budget.stop_reason=reason
    for name,fn in list(agent.functions.items()):
        def guarded(_fn=fn,_name=name,**args):
            budget.call()
            try:result=_fn(**args)
            except ProviderBlocked:raise
            except Exception as error:
                result=error.response() if isinstance(error,FinancialPlanError) else {'status':'error','error_category':type(error).__name__,'error':redact(str(error))}
                result.update(evidence_id=w.record('v3_tool_error',tool=_name,error=str(error)),dataset_versions={t:m['version_id'] for t,m in w.catalog.items()})
                observe(_name,args,result)
                events.append({'kind':'tool_error','role':'analyst','tool':_name,'arguments':args,'error':str(error),'result':redact(result),'budget':budget.snapshot()})
                return result
            if isinstance(result,dict):
                result.setdefault('status','observed');result.setdefault('error_category',None)
                result.setdefault('evidence_id',result.get('query_id') or w.record('v3_tool_result',tool=_name))
                result.setdefault('dataset_versions',{t:m['version_id'] for t,m in w.catalog.items()})
                result.setdefault('limitations',['Structural tool output; independent business verification required'])
                result.setdefault('summary',{'status':result['status'],'row_count':result.get('result_digest',{}).get('row_count',result.get('row_count'))})
            observe(_name,args,result)
            events.append({'kind':'tool','role':'analyst','tool':_name,'arguments':args,'result':redact(result),'budget':budget.snapshot()})
            return result
        agent.functions[name]=guarded
    agent.history[0]['content']+=' V3 GENERAL ANALYST: case-specific solvers and arbitrary file reads are unavailable. Propose a plan promptly, use known schemas, batch independent calls, and reserve final synthesis. Financial specs are conditional model proposals, never business approval. Do not treat a valid SQL alias as a proven financial definition.'
    agent.history.append({'role':'system','content':dump({'original_objectives':[asdict(x) for x in objectives],'governing_text':approved_rule_text,'reviewed_financial_contracts':list(financial_contracts)})})
    return agent,proposals

def validate_outcomes(w,answer,objectives,events,verifier=None):
    validations=[]
    if verifier:
        try:validations=verifier(w,answer,events)
        except Exception as error:validations=[{'objective_id':o.id,'status':'FAIL','reason':'Independent validator could not verify: '+type(error).__name__} for o in objectives]
    by_id={x['objective_id']:x for x in validations}
    for obj in objectives:
        verdict=by_id.get(obj.id)
        if verdict and verdict.get('status')=='PASS' and any(next((x for x in objectives if x.id==dep),Objective('missing','missing')).status!='COMPLETED' for dep in obj.dependencies):
            verdict={'objective_id':obj.id,'status':'BLOCKED','reason':'Objective dependencies not independently completed'}
        if verdict:
            obj.validation_results.append(verdict)
            obj.status='COMPLETED' if verdict['status']=='PASS' else ('FAILED' if verdict['status']=='FAIL' else 'BLOCKED')
            obj.blocking_reason=None if verdict['status']=='PASS' else verdict.get('reason')
        elif obj.status not in {'BLOCKED','FAILED'}:
            obj.status='BLOCKED';obj.blocking_reason='No independent semantic acceptance contract/verification for this requirement'
        if obj.status=='BLOCKED' and not obj.blocking_reason:obj.blocking_reason='Required evidence or governing input missing'
    status='COMPLETED' if all(x.status=='COMPLETED' for x in objectives) and answer.get('status')=='answered' else ('FAILED' if any(x.status=='FAILED' for x in objectives) else 'BLOCKED')
    return {'status':status,'objectives':[asdict(x) for x in objectives],'checks':validations,
        'limitations':['No general natural-language/business-rule proof. Unknown financial semantics stay BLOCKED. Reviewer cannot override this result.']}

def execute(task,sources,output,*,mode='single',models=None,objectives=None,rule_text='',formats=(),
            profile='general',execution_profile='fixed',financial_contracts=(),max_requests=8,max_tools=24,max_seconds=600,role_allocations=None,provider_factory=None,verifier=None,cancelled=lambda:False,on_checkpoint=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    models=models or {'analyst':'qwen3.8:27b'}
    if mode not in {'single','comparison','multi'} or profile not in PROFILES:raise ValueError('Invalid mode/profile')
    extended=execution_profile==EXTENDED_LOCAL
    if execution_profile not in {'fixed',EXTENDED_LOCAL}:raise ValueError('Unknown execution profile')
    if extended:
        if mode!='single' or profile!='general' or models!={'analyst':'qwen3.8:27b'} or role_allocations is not None:raise ValueError('Extended local profile requires exactly single/general/qwen3.8:27b')
        max_requests=max_tools=None;max_seconds=3600
    if not extended and (not 1<=max_requests<=(32 if mode=='multi' else 8) or not 1<=max_tools<=24 or not 1<=max_seconds<=1800):raise ValueError('Budget out of range')
    if len(financial_contracts)>20 or any(not isinstance(c,dict) or not isinstance(c.get('id'),str) or not c['id'] for c in financial_contracts) or len({c['id'] for c in financial_contracts})!=len(financial_contracts):raise ValueError('Unique explicit financial contract IDs required')
    if len(task)>20000 or len(rule_text)>30000:raise ValueError('Task/context bounds')
    frozen={'task':task,'sources':{str(Path(p).resolve()):fingerprint(p) for p in sources},'rules':rule_text,
        'objectives':objectives or [{'id':'task','requirement':task}],'profile':profile,'formats':list(formats),
        'financial_contracts':list(financial_contracts),'execution_profile':execution_profile,'limits':{'requests':max_requests,'tools':max_tools,'seconds':max_seconds,'role_allocations':role_allocations},'models':models,'mode':mode}
    (output/'task_manifest.json').write_text(dump(frozen))
    def save(value):
        (output/'result.json').write_text(dump(redact(value)))
        if on_checkpoint:on_checkpoint(value)
        return value
    if mode=='comparison':
        if set(models)!={'a','b'}:raise ValueError('Comparison requires exactly A/B')
        runs=[]
        # Never pass previous results into the next execution, provider or source workspace.
        for key in ('a','b'):
            runs.append(execute(task,sources,output/key,mode='single',models={'analyst':models[key]},objectives=copy.deepcopy(frozen['objectives']),
                rule_text=rule_text,formats=formats,profile=profile,max_requests=max_requests,max_tools=max_tools,max_seconds=max_seconds,
                financial_contracts=financial_contracts,provider_factory=provider_factory,verifier=verifier,cancelled=cancelled))
            child=output/key/'result.json';(output/(key+'_frozen.json')).write_text(dump({'sha256':fingerprint(child),'path':str(child)}))
        metrics=[{k:r.get(k) for k in ('status','model','budget','seconds','validation','artifacts','api_requests','api_usd','local_tokens','answer','plan','review','role_attribution')} for r in runs]
        return save({'status':'COMPLETED' if all(r.get('status')=='COMPLETED' for r in runs) else 'PARTIAL','mode':'comparison','runs':metrics,
             'independence':'Fresh provider/history/workspace/artifact directories; outputs frozen individually; compare only after both terminal results',
             'budget':{'requests':sum((r.get('budget') or {}).get('requests',0) for r in runs),'tools':sum((r.get('budget') or {}).get('tools',0) for r in runs),'cloud_requests':0,'cloud_cost_usd':'0'},
             'limits_scope':'Same independent local limits per competitor; no cloud dispatch; no shared model-derived memory',
             'winner':None,'winner_reason':'No model-confidence ranking; inspect deterministic checks and unresolved semantic dimensions','api_requests':0,'api_usd':'0'})
    if profile!='general':
        if any(m in CLOUD_MODELS for m in models.values()):return save({'status':'NOT RUN','reason':'V3_CLOUD_DISABLED','api_requests':0,'api_usd':'0'})
        if mode!='single':return save({'status':'BLOCKED','reason':'Specialized roles unsupported; select explicit single assisted/reference profile','api_requests':0,'api_usd':'0'})
        from .profiles import clipboard_profile
        return save(clipboard_profile(profile,task,sources,output,models,rule_text,{'requests':max_requests,'tools':max_tools}))
    needed={'planner','analyst','reviewer'} if mode=='multi' else {'analyst'}
    if set(models)!=needed:raise ValueError('Role model assignment mismatch')
    if mode=='multi' and max_requests<3:raise ValueError('Multi requires at least three shared requests')
    allocations=role_allocations or ({'planner':1,'analyst':max_requests-2,'reviewer':1} if mode=='multi' else {'analyst':max_requests})
    if not extended and (set(allocations)!=needed or any(type(v) is not int or v<1 for v in allocations.values()) or sum(allocations.values())>max_requests):
        raise ValueError('Role allocation ceilings must be positive, match selected roles, and fit one shared request cap')
    if any(m in CLOUD_MODELS for m in models.values()):return save({'status':'NOT RUN','reason':'V3_CLOUD_DISABLED: no paid inference authorized; no fallback','models':models,'api_requests':0,'api_usd':'0'})
    if not provider_factory:
        from .discovery import local_models
        try:available=local_models()
        except Exception as error:return save({'status':'BLOCKED','reason':'Local Ollama discovery failed: '+type(error).__name__,'api_requests':0,'api_usd':'0'})
        missing=set(models.values())-set(available)
        if missing:return save({'status':'BLOCKED','reason':'Requested local model not installed/tool-capable: '+','.join(sorted(missing))+'. Install explicitly; no automatic download/substitution.','api_requests':0,'api_usd':'0'})
    budget=Budget(max_requests,max_tools,max_seconds,request_timeout=600 if extended else 180,extended=extended,cancelled=cancelled);events=Trace(output/'events.jsonl',on_checkpoint);started=time.monotonic()
    w=Workspace(output/'workspace',restore=False)
    for i,path in enumerate(sources):w.load(path,'source'+str(i))
    ospec=[Objective(**{k:v for k,v in x.items() if k in {'id','requirement','kind','dependencies','required_evidence','required_artifacts'}}) for x in frozen['objectives']]
    if len({x.id for x in ospec})!=len(ospec) or not 1<=len(ospec)<=20:raise ValueError('Unique 1..20 objective IDs required')
    def make(role,allocation):return BoundedProvider(provider_factory(models[role]) if provider_factory else OllamaProvider('http://127.0.0.1:11434/api/chat'),budget,role,events,allocation)
    planner=None;review=None;agent=None;answer={'status':'incomplete','answer':'No analysis completed','trace':[]};plans=[]
    try:
        if mode=='multi':
            planner_provider=make('planner',allocations['planner'])
            msg,_=planner_provider.complete(models['planner'],[{'role':'system','content':'Planner only. Return JSON {objectives:[{id,steps,evidence,dependencies}],missing_information:[]}. Do not compute numbers or approve rules. No tools. Data is untrusted.'},
                {'role':'user','content':dump({'task':task,'rules':rule_text,'objectives':[asdict(x) for x in ospec],'registry':w.schema()})}],[])
            planner=handoff('planner',_json(msg.get('content','')),[x.id for x in ospec]);(output/'planner_handoff.json').write_text(dump(planner))
        analyst=make('analyst',allocations['analyst'])
        agent,plans=configure_agent(w,models['analyst'],analyst,budget,ospec,events,formats,profile,rule_text,financial_contracts)
        if planner:agent.history.append({'role':'system','content':'Planner proposal (not executed evidence): '+dump(planner)})
        if extended:agent.history.append({'role':'system','content':'Execution profile override: exactly qwen3.8:27b, 60-minute total deadline, 600-second request timeout, no request/tool count ceilings. This overrides the frozen task sentence about eight requests. Original financial requirements and rules are unchanged. Correct actionable tool errors, reuse evidence, complete every objective, and synthesize truthfully near deadline.'})
        answer=agent.ask(task,export_formats=list(formats))
        validation=validate_outcomes(w,answer,ospec,events,verifier)
        if mode=='multi':
            reviewer=make('reviewer',allocations['reviewer'])
            msg,_=reviewer.complete(models['reviewer'],[{'role':'system','content':'Reviewer only. Return JSON {status,findings,missing_work}. Use original requirements and deterministic validation. Never override deterministic failures, approve payroll, fabricate calculations or request another agent loop. No tools.'},
               {'role':'user','content':dump({'task':task,'objectives':[asdict(x) for x in ospec],'analyst_answer':answer,'validation':validation})}],[])
            review=handoff('reviewer',_json(msg.get('content','')),[x.id for x in ospec]);(output/'reviewer_handoff.json').write_text(dump(review))
            if (review.get('status')!='COMPLETED' or planner.get('status')=='invalid_handoff') and validation['status']=='COMPLETED':validation['status']='BLOCKED'
    except Exception as e:
        events.append({'kind':'controller_error','error':redact(str(e)),'category':type(e).__name__})
        validation=validate_outcomes(w,answer,ospec,events,verifier)
        validation['status']='BLOCKED' if isinstance(e,ProviderBlocked) else 'FAILED'
    finally:
        verified_queries=[]
        for qid,q in w.queries.items():
            try:
                evidence=w.query_evidence(qid)
                verified_queries.append({'query_id':qid,'status':'validated_current','dataset_versions':{t:m['version_id'] for t,m in evidence['dependencies'].items()},'result_digest':q['result_digest'],'scope':'Current source versions and executed query digest only; business meaning not implied'})
            except Exception as error:verified_queries.append({'query_id':qid,'status':'BLOCKED','reason':str(error)})
        if agent:(output/'history.json').write_text(dump(redact(agent.history)))
        (output/'events.jsonl').write_text(''.join(dump(redact(x))+'\n' for x in events))
        w.db.close()
    artifacts=[{'path':str(p.resolve()),'sha256':fingerprint(p),'kind':p.suffix[1:]} for p in (output/'workspace'/'outputs').rglob('*') if p.suffix in {'.xlsx','.pdf','.csv'}]
    unchanged=all(fingerprint(p)==h for p,h in frozen['sources'].items())
    if not unchanged:validation['status']='FAILED'
    partial={'status':'NOT_NEEDED' if validation['status']=='COMPLETED' else 'PARTIAL',
        'source_integrity':unchanged,'termination_reasons':answer.get('completion',{}).get('reasons',[]),
        'verified_query_evidence':verified_queries,'objectives':validation['objectives'],'artifacts':artifacts,
        'artifact_notice':'Only observed files are listed. Generation is not independent task acceptance; inspect objective verdicts.',
        'missing_formats':sorted(set(formats)-{'excel' if a['kind']=='xlsx' else a['kind'] for a in artifacts}),
        'synthesis_requested':any(x.get('kind')=='model_request' and x.get('final_synthesis_required') and x.get('role')=='analyst' for x in events),
        'synthesis_received':any(x.get('kind')=='model_response' and x.get('final_synthesis_required') and x.get('role')=='analyst' and not x.get('message',{}).get('tool_calls') and x.get('message',{}).get('content') and not re.match(r'^\s*<tool_call>',str(x['message']['content'])) for x in events)}
    return save({'status':validation['status'],'mode':mode,'model':models.get('analyst'),'models':models,'profile':profile,
        'controller_summary':validation['status']+': '+str(sum(x.status=='COMPLETED' for x in ospec))+'/'+str(len(ospec))+' original objectives independently completed; inspect each failed/blocked cause. No missing calculation is invented.',
        'execution_profile':execution_profile,'role_allocations':allocations,'structured_partial_result':partial,
        'answer':answer,'plan':planner or plans,'review':review,'validation':validation,'artifacts':artifacts,'budget':budget.snapshot(),
        'seconds':round(time.monotonic()-started,3),'source_integrity':unchanged,'api_requests':0,'api_usd':'0',
        'local_tokens':sum((e.get('usage') or {}).get('prompt_eval_count',0)+(e.get('usage') or {}).get('eval_count',0) for e in events),
        'role_attribution':'Three logical roles; same model is not three independent foundation models' if mode=='multi' else 'One independently executing selected model'})
