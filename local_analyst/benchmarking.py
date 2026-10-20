"""Sequential, once-per-model challenge runner with independent evidence grading.

Cloud entries remain NOT RUN unless an explicit approved provider is supplied by a
trusted caller. No fallback, hidden reference totals, or repeat trial is implemented.
"""
from pathlib import Path
from datetime import datetime, timezone
import json
import time
from .agent import AnalystAgent, tool
from .workspace import Workspace, dump, fingerprint
from .providers import CLOUD_MODELS, redact

TESTS=['A schema','B SQL','C relationships','D rules','E commissions','F exceptions and impact',
       'G scenario','H Slack escalations','I dynamic Excel','J PDF consistency']
MODELS=('qwen3:8b','qwen3.8:27b',*CLOUD_MODELS)


def run_case(model, workbook, rules, output, *, provider=None, cancelled=None, on_event=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    if model in CLOUD_MODELS and provider is None:
        result={'model':model,'status':'NOT RUN','reason':'Paid execution and actual-case data exposure are not authorized',
                'api_requests':0,'input_tokens':0,'output_tokens':0,'api_usd':'0','objectives':{t:'NOT RUN' for t in TESTS}}
        (output/'result.json').write_text(dump(result));return result
    if not Path(rules).is_file():
        return {'model':model,'status':'BLOCKED','reason':'ORIGINAL_RULES_MISSING'}
    hashes={str(p):fingerprint(p) for p in map(Path,(workbook,rules))}
    workspace=Workspace(output/'workspace',restore=False);workspace.load(workbook,'case')
    events=[];candidate_result=None;current_call=None
    def event(kind,payload):
        nonlocal current_call
        if kind=='tool_call_started':current_call=payload.get('call_id')
        payload={'model':model,**payload}
        record=redact({'kind':kind,'payload':payload});events.append(record)
        with (output/'events.jsonl').open('a') as f:f.write(dump(record)+'\n')
        if on_event:on_event(kind,payload)
    def audit(record):
        if record['kind']=='query':
            event('sql_executed',{'call_id':current_call,'query_id':record['event_id'],'sql':record['sql'],
              'parameters':record['parameters'],'result':record['result'],'result_digest':record['result_digest'],
              'dataset_versions':{t:m['version_id'] for t,m in record['dependencies'].items()}})
    workspace.on_record=audit
    agent=AnalystAgent(workspace,model=model,provider=provider,on_event=event,should_cancel=cancelled)
    def rules_tool():return {'text':Path(rules).read_text(),'sha256':hashes[str(Path(rules))]}
    def build():
        nonlocal candidate_result
        from examples.clipboard.v2 import candidate
        candidate_result=candidate(workbook,rules,workspace.root/'outputs'/'case_candidate')
        return candidate_result
    agent.functions['inspect_case_rules']=rules_tool
    agent.functions['build_case_candidate']=build
    agent.tools += [tool('inspect_case_rules','Read the complete original case instructions and governing compensation rules; no historical answer key.'),
                    tool('build_case_candidate','Run the existing explicit CONDITIONAL case calculation locally and compile the reviewed tier-pricing expressions with generic Financial Model Builder. Returns real Excel/PDF paths and separate question validation. Source coverage/approval and unresolved exception impacts remain BLOCKED/FAIL. This tool does not approve payroll. Requires explicit Excel/PDF request.')]
    # Constrain file tools to exact benchmark inputs; no arbitrary local reads.
    agent.functions['inspect_rules']=lambda path:rules_tool() if Path(path).resolve()==Path(rules).resolve() else (_ for _ in ()).throw(ValueError('Unapproved rules path'))
    agent.functions['load_data']=lambda **args: (_ for _ in ()).throw(ValueError('Benchmark source is already loaded; no additional files authorized'))
    prompt=('Complete the original Clipboard Health case as one integrated workflow. Read inspect_case_rules. '
       'Inspect schemas, execute a genuine source SQL calculation, and inspect an explicit multi-table relationship. '
       'Calculate June compensation for all 80 roster reps; size and rank exceptions without double counting; '
       'reprice SMB AE accelerators to 1.3x/1.5x; select exactly three actual Slack messages and write one English response under 250 words. '
       'Create a dynamic Excel workbook and a PDF summary of at most three pages. The deterministic build_case_candidate tool is available for an explicitly conditional calculation and real artifacts. '
       'Review its independent checks and clearly report every blocked or failed objective. Do not label plan calculations actual paid or approve unresolved rules. '
       'Use the existing bounded steps and tools. Never claim full success unless all four objectives and deliverables pass.')
    started=time.monotonic()
    try:answer=agent.ask(prompt,export_formats=['excel','pdf'])
    except Exception as e:answer={'status':'incomplete','answer':'Benchmark failed: '+type(e).__name__,'trace':[]}
    duration=time.monotonic()-started
    trace=answer.get('trace',[])
    queries=[i['result'] for i in trace if i['tool']=='query_sql' and i['result'].get('dataset_versions')]
    schema=any(i['tool']=='inspect_schema' and i['result'].get('status')!='error' for i in trace)
    relationships=any(i['tool']=='check_join' and i['result'].get('status')!='error' for i in trace)
    rule=any(i['tool']=='inspect_case_rules' for i in trace)
    cv=(candidate_result or {}).get('validation',{})
    scores={'A schema':'PASS' if schema else 'FAIL','B SQL':'PASS' if queries else 'FAIL',
       'C relationships':'PASS' if relationships else 'FAIL','D rules':'BLOCKED' if rule else 'FAIL',
       'E commissions':cv.get('Q1',{}).get('status','FAIL'),'F exceptions and impact':cv.get('Q2',{}).get('status','FAIL'),
       'G scenario':cv.get('Q3',{}).get('status','FAIL'),'H Slack escalations':cv.get('Q4',{}).get('status','FAIL'),
       'I dynamic Excel':'PASS' if cv.get('pdf_excel_consistent') else 'FAIL',
       'J PDF consistency':'PASS' if cv.get('pdf_excel_consistent') else 'FAIL'}
    # SQL execution is structural evidence; business correctness is never inferred from it.
    tokens=[e['payload'].get('usage') or {} for e in events if e['kind']=='model_response_received']
    result={'model':model,'status':'PASS' if all(v=='PASS' for v in scores.values()) else 'FAIL',
       'stop_reasons':answer.get('completion',{}).get('reasons',[]),
       'agent_status':answer.get('status'),'objectives':scores,'automatic_score_scope':'Structural evidence and explicit candidate checks; independent business accuracy remains BLOCKED',
       'independent_complete_answer_accuracy':'BLOCKED','seconds':round(duration,3),
       'tool_calls':len(trace),'model_requests':sum(e['kind']=='model_request_started' for e in events),
       'tokens':sum(t.get('prompt_eval_count',0)+t.get('eval_count',0) for t in tokens) if model not in CLOUD_MODELS else None,
       'cloud_usage':provider.ledger.summary(provider.run)['models'][model] if provider else None,
       'api_requests':0 if model not in CLOUD_MODELS else provider.ledger.summary(provider.run)['models'][model]['requests'],
       'api_usd':'0' if model not in CLOUD_MODELS else provider.ledger.summary(provider.run)['models'][model]['observed_estimate_usd'],
       'candidate':candidate_result,'answer':answer,'source_preservation':all(fingerprint(p)==sha for p,sha in hashes.items())}
    if not result['source_preservation']:
        result.update(status='FAIL',reason='SOURCE_CHANGED')
    if 'BUDGET_EXCEEDED' in result['stop_reasons']:
        result.update(status='BLOCKED',reason='BUDGET_EXCEEDED')
    (output/'history.json').write_text(dump(redact(agent.history)))
    (output/'result.json').write_text(dump(redact(result)))
    workspace.db.close()
    return result


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--workbook',required=True);p.add_argument('--rules',required=True)
    p.add_argument('--output',required=True);p.add_argument('--models',nargs='+',default=list(MODELS));a=p.parse_args()
    root=Path(a.output);root.mkdir(parents=True,exist_ok=False)
    results=[]
    for m in a.models:
        results.append(run_case(m,a.workbook,a.rules,root/m.replace(':','_').replace('.','_')))
        (root/'comparison.json').write_text(dump({'results':results,'trials_per_model':1,'retries':0}))
    print(dump([{'model':r['model'],'status':r['status'],'api_usd':r.get('api_usd')} for r in results]))
if __name__=='__main__':main()
