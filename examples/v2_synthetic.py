"""One sequential local-only dynamic workbook pipeline attempt per model."""
import json
from pathlib import Path
import time
from decimal import Decimal
from local_analyst import Workspace
from local_analyst.agent import AnalystAgent
from local_analyst.workspace import dump
from openpyxl import load_workbook


def run(model, root, *, provider=None):
    if model.startswith("gpt-") and provider is None:raise ValueError("Cloud provider with explicit approval required")
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    source=root/'inputs.csv';source.write_text('id,amount\na,100\nb,200\n')
    spec={'version':1,'source_key':'id','rule_ref':'Synthetic reviewed contract: base=amount*10%; scenario=base*multiplier; row delta=scenario-base',
      'parameters':{'multiplier':{'value':'1.2','description':'Scenario multiplier'}},'calculations':[
       {'id':'base','expression':{'mul':[{'field':'amount'},'0.1']}},
       {'id':'scenario','expression':{'mul':[{'field':'base'},{'param':'multiplier'}]}},
       {'id':'delta','expression':{'sub':[{'field':'scenario'},{'field':'base'}]}}],'totals':['base','scenario','delta']}
    plan=root/'reviewed_plan.json';plan.write_text(dump({'plan_id':'synthetic_model','financial_model':spec}))
    w=Workspace(root/'workspace');w.load(source);pid=w.register_plan(plan);events=[]
    def event(kind,payload):
        events.append({'kind':kind,'payload':payload})
        with (root/'events.jsonl').open('a') as f:f.write(dump(events[-1])+'\n')
    a=AnalystAgent(w,model=model,on_event=event,provider=provider)
    # Cloud fixture execution cannot read or load any unrelated project/business file.
    a.functions['load_data']=lambda **kwargs: (_ for _ in ()).throw(ValueError('Synthetic scope: no additional source loading authorized'))
    def rules(path):
        if Path(path).resolve()!=plan.resolve():raise ValueError('Synthetic scope: unapproved rule path')
        return {'text':plan.read_text()}
    a.functions['inspect_rules']=rules
    prompt=f'Use tools to execute SQL that returns id and amount from the registered input. Create a dynamic Excel model using build_financial_model with user-registered plan_id {pid}. Wait for the actual query_id before building. Report completion with the artifact evidence. Do not invent formulas, register plans, or substitute a snapshot. The reviewed synthetic specification is already registered.'
    start=time.monotonic();answer=a.ask(prompt,export_formats=['excel'])
    artifacts=[i['result'] for i in answer.get('trace',[]) if i['tool']=='build_financial_model' and i['result'].get('status')=='exported']
    checks=[]
    for artifact in artifacts:
        b=load_workbook(artifact['path'],data_only=True)
        checks.append([Decimal(str(b['Summary'][f'B{r}'].value)) for r in (2,3,4)]==[Decimal(30),Decimal(36),Decimal(6)])
    result={'model':model,'status':'PASS' if checks and all(checks) and answer['status']=='answered' else 'FAIL',
       'artifact_checks':checks,'seconds':round(time.monotonic()-start,3),'requests':sum(e['kind']=='model_request_started' for e in events),
       'api_requests':provider.ledger.summary(provider.run)['models'][model]['requests'] if provider else 0,
       'api_usd':provider.ledger.summary(provider.run)['models'][model]['observed_estimate_usd'] if provider else '0',
       'cloud_usage':provider.ledger.summary(provider.run)['models'][model] if provider else None,'answer':answer}
    (root/'result.json').write_text(dump(result));(root/'history.json').write_text(dump(a.history));w.db.close()
    return result


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--models',nargs='+',default=['qwen3:8b','qwen3.8:27b']);a=p.parse_args()
    if any(m.startswith('gpt-') for m in a.models):raise ValueError('This synthetic runner is local-only')
    results=[]
    for m in a.models:
        results.append(run(m,Path(a.output)/m.replace(':','_').replace('.','_')))
        (Path(a.output)/'summary.json').write_text(dump(results))
    print(dump([{'model':r['model'],'status':r['status'],'requests':r['requests']} for r in results]))
if __name__=='__main__':main()
