"""Offline controllers/real local tools. No live foundation-model score claims."""
import json,tempfile,unittest,socket,hashlib,copy
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal as D
from local_analyst.v3 import execute,Budget,BoundedProvider,sql_check,Objective,validate_outcomes
from local_analyst.providers import ProviderBlocked
from local_analyst.registry import model_registry
from local_analyst.workspace import Workspace
from examples.v3_benchmark import create,OBJECTIVES,RULES,TASK,verifier

class Script:
    def __init__(self,fn):self.fn=fn;self.requests=[]
    def complete(self,model,history,tools):
        self.requests.append(copy.deepcopy(history));return self.fn(len(self.requests),history,tools),{'usage':{'prompt_eval_count':1,'eval_count':1}}
def calls(*entries):return {'role':'assistant','content':'','tool_calls':[{'id':'c'+str(i),'function':{'name':name,'arguments':args}} for i,(name,args) in enumerate(entries)]}
def done(text='Evidence checked; missing inputs are held, not approved payroll.'):return {'role':'assistant','content':text}

def scripted_financial():
    ids={};query=None;plan=None
    def fn(n,h,tools):
        nonlocal query,plan
        for m in h:
            if m.get('role')!='tool':continue
            result=json.loads(m['content']).get('result',{})
            if 'tables' in result:ids.update({Path(x['source_path']).name:x['table_id'] for x in result['tables']})
            if result.get('query_id') and result.get('columns') and 'commission_usd' in result['columns']:query=result['query_id']
            if result.get('plan_id'):plan=result['plan_id']
        if n==1:return calls(('set_task_plan',{'items':[{'id':x['id'],'steps':['inspect','query','verify']} for x in OBJECTIVES]}),('inspect_schema',{}))
        orders=next(v for k,v in ids.items() if 'Orders' in k);customers=next(v for k,v in ids.items() if 'Customers' in k);fx=next(v for k,v in ids.items() if 'FX' in k)
        cte=f'WITH c AS (SELECT customer_id, MIN(currency) AS currency FROM "{customers}" GROUP BY customer_id) '
        base=f'FROM "{orders}" o JOIN c ON o.customer_id=c.customer_id LEFT JOIN "{fx}" f ON c.currency=f.currency WHERE o.status=\'earned\' AND o.order_date>=\'2026-06-01\' AND o.order_date<\'2026-07-01\''
        sql=cte+'SELECT o.order_id,o.net_usd,f.units_per_usd,ROUND(o.net_usd*.1,2) AS commission_usd,ROUND(o.net_usd*.1*f.units_per_usd,2) AS commission_local,ROUND(o.net_usd*.125,2) AS scenario_usd,ROUND(o.net_usd*.125*f.units_per_usd,2) AS scenario_local,ROUND(o.net_usd*.125,2)-ROUND(o.net_usd*.1,2) AS delta_usd '+base+' AND f.units_per_usd IS NOT NULL'
        if n==2:return calls(('check_join',{'left_table':orders,'right_table':customers,'left_on':['customer_id'],'right_on':['customer_id']}),('query_sql',{'sql':sql}))
        spec={'version':1,'source_key':'order_id','rule_ref':'Explicit synthetic governing rules','parameters':{'scenario_rate':{'value':'.125'}},'calculations':[
            {'id':'commission_usd','expression':{'round':[{'mul':[{'field':'net_usd'},'.1']},2]}},
            {'id':'commission_local','expression':{'round':[{'mul':[{'field':'commission_usd'},{'field':'units_per_usd'}]},2]}},
            {'id':'scenario_usd','expression':{'round':[{'mul':[{'field':'net_usd'},{'param':'scenario_rate'}]},2]}},
            {'id':'scenario_local','expression':{'round':[{'mul':[{'field':'scenario_usd'},{'field':'units_per_usd'}]},2]}},
            {'id':'delta_usd','expression':{'sub':[{'field':'scenario_usd'},{'field':'commission_usd'}]}}],'totals':['commission_usd','scenario_usd','delta_usd']}
        # Inputs must not collide with ordered calculation names.
        input_sql=cte+'SELECT o.order_id,o.net_usd,f.units_per_usd '+base+' AND f.units_per_usd IS NOT NULL'
        if n==3:return calls(('query_sql',{'sql':input_sql}),('query_sql',{'sql':cte+'SELECT o.order_id,c.currency,o.net_usd '+base+' AND f.units_per_usd IS NULL'}),('query_sql',{'sql':f'SELECT customer_id,COUNT(*) AS copies FROM "{customers}" GROUP BY customer_id HAVING COUNT(*)>1'}))
        input_query=next(json.loads(m['content'])['result']['query_id'] for m in reversed(h) if m.get('role')=='tool' and json.loads(m['content']).get('result',{}).get('columns')==['order_id','net_usd','units_per_usd']) if n>=4 else None
        if n==4:return calls(('define_financial_plan',{'spec':spec,'query_id':input_query}))
        if n==5:return calls(('build_financial_model',{'plan_id':plan,'query_id':input_query}),('export_report',{'query_id':query,'title':'Synthetic financial findings','paragraphs':['June commission 36 USD; scenario 45 USD; delta 9 USD. Missing JPY FX is held, duplicate customer mapping canonicalized. Query '+query]}))
        return done('June commission 36; scenario 45; delta 9. Missing JPY FX hold; duplicate mapping excluded. '+query)
    return Script(fn)

class V3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        # OS/sandbox network isolation plus an explicit socket guard: no SDK path can escape mocks.
        self.net=patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK_ISOLATED_TEST'));self.net.start();self.addCleanup(self.net.stop)
    def sources(self):return create(self.root/'sources')
    def test_registry_unknown_limits_no_substitution(self):
        r=model_registry([{'id':'qwen3.8:27b','local':True}],connected=True)
        self.assertIsNone(next(x for x in r if x['id']=='qwen3:8b')['supports_tools'])
        self.assertFalse(next(x for x in r if x['id']=='gpt-6-luna')['available'])
        self.assertTrue(all(x['native_context_limit'] is None for x in r))
    def test_invalid_columns_and_file_sql_fail_before_query(self):
        w=Workspace(self.root/'w');self.addCleanup(w.db.close)
        import pandas as pd
        w.register(pd.DataFrame({'id':[1],'amount':[10]}),'sales',{'kind':'fixture'})
        d=sql_check(w,'SELECT missing_rate FROM sales');self.assertEqual(d['status'],'error');self.assertIn('amount',d['available_columns']['sales'])
        self.assertEqual(sql_check(w,"SELECT * FROM read_csv('/etc/passwd')")['status'],'error')
        self.assertEqual(sql_check(w,'SELECT amount AS value FROM sales')['status'],'validated')
    def test_shared_budget_across_roles_and_final_synthesis(self):
        b=Budget(max_requests=2,max_tools=1);events=[];p=BoundedProvider(Script(lambda n,h,t:done()),b,'planner',events,1);p.complete('qwen3.8:27b',[],[{'tool':'x'}]);self.assertEqual(events[0]['tools'],[])
        BoundedProvider(Script(lambda n,h,t:done()),b,'reviewer',events,1).complete('qwen3.8:27b',[],[])
        with self.assertRaises(ProviderBlocked):b.request()
        b.call()
        with self.assertRaises(ProviderBlocked):b.call()
    def test_cloud_never_constructs_sdk_or_exposes_sources(self):
        with patch('openai.OpenAI',side_effect=AssertionError('SDK_FORBIDDEN')):
            r=execute('case',[],self.root/'cloud',models={'analyst':'gpt-6-luna'})
        self.assertEqual(r['status'],'NOT RUN');self.assertEqual(r['api_requests'],0)
    def test_comparison_isolation_no_answer_memory(self):
        sources=self.sources();instances=[]
        def factory(model):
            p=Script(lambda n,h,t:done('PRIVATE_'+model));instances.append(p);return p
        r=execute('same task',sources,self.root/'comparison',mode='comparison',models={'a':'a-local','b':'b-local'},provider_factory=factory)
        self.assertEqual(len(instances),2);self.assertNotIn('PRIVATE_a-local',json.dumps(instances[1].requests))
        for label in ('a','b'):
            f=json.loads((self.root/'comparison'/f'{label}_frozen.json').read_text());self.assertEqual(f['sha256'],hashlib.sha256(Path(f['path']).read_bytes()).hexdigest())
        self.assertEqual(r['winner'],None)
    def test_reviewer_confidence_cannot_override_failure(self):
        sources=self.sources()
        r=execute('calculate',sources,self.root/'multi',mode='multi',models={k:'local' for k in ('planner','analyst','reviewer')},objectives=[{'id':'x','requirement':'money'}],max_requests=3,
          provider_factory=lambda _:Script(lambda n,h,t:done('{"status":"PASS","findings":[]}')),
          verifier=lambda *args:[{'objective_id':'x','status':'FAIL','reason':'wrong metric'}])
        self.assertEqual(r['status'],'FAILED');self.assertEqual(r['budget']['requests'],3)
    def test_financial_semantics_without_contract_blocked(self):
        r=execute('approved commissions',self.sources(),self.root/'semantics',provider_factory=lambda _:Script(lambda n,h,t:done('COUNT(*) is approved commission.')))
        self.assertEqual(r['status'],'BLOCKED');self.assertIn('semantic',r['validation']['objectives'][0]['blocking_reason'])
    def test_general_tools_do_not_offer_case_adapter(self):
        scripts=[]
        def factory(_):
            p=Script(lambda n,h,t:done());scripts.append(p);return p
        execute('inspect',self.sources(),self.root/'generic',provider_factory=factory,max_requests=2)
        text=(self.root/'generic/events.jsonl').read_text();self.assertNotIn('build_case_candidate',text);self.assertNotIn('inspect_rules',text)
    def test_real_generic_sql_to_formula_model_pipeline_scripted_not_live(self):
        sources=self.sources();p=scripted_financial()
        result=execute(TASK,sources,self.root/'pipeline',models={'analyst':'scripted-local'},objectives=OBJECTIVES,rule_text=RULES,formats=['excel','pdf'],provider_factory=lambda _:p,verifier=verifier(sources))
        self.assertEqual(result['status'],'COMPLETED',json.dumps(result['validation']))
        self.assertEqual(result['budget']['requests'],6);self.assertTrue(any(a['kind']=='xlsx' for a in result['artifacts']))
    def test_unknown_rule_text_blocks_model_plan_approval(self):
        from local_analyst.v3 import configure_agent
        w=Workspace(self.root/'empty');self.addCleanup(w.db.close);b=Budget();p=BoundedProvider(Script(lambda n,h,t:done()),b,'analyst',[],8)
        a,_=configure_agent(w,'local',p,b,[],[],['excel'])
        result=a.functions['define_financial_plan'](spec={},query_id='invented');self.assertEqual(result['status'],'error');self.assertIn('governing',result['error'])
    def test_cancel_and_time_limits(self):
        with self.assertRaisesRegex(ProviderBlocked,'CANCELLED'):Budget(cancelled=lambda:True).request()
        b=Budget(max_seconds=1);b.start-=2
        with self.assertRaisesRegex(ProviderBlocked,'WALL_TIME'):b.call()


class V3HTTPTests(unittest.TestCase):
    def setUp(self):
        from web_api.app import create_app
        from fastapi.testclient import TestClient
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.app=create_app(self.root/'state',[self.root]);self.m=self.app.state.manager;self.m.models=lambda:{'models':[{'id':'qwen3.8:27b','local':True}],'available':True}
        self.client=TestClient(self.app,base_url='http://localhost');self.client.__enter__();self.addCleanup(lambda:self.client.__exit__(None,None,None))
        b=self.client.get('/api/bootstrap').json();self.client.headers['x-csrf-token']=b['csrf_token'];self.w=self.client.post('/api/workspaces',json={'name':'V3 isolated'}).json()['id']
        self.source=self.client.post(f'/api/workspaces/{self.w}/upload',files={'file':('x.csv',b'id,amount\n1,100\n')}).json()
        self.f=self.client.get(f'/api/workspaces/{self.w}').json()['files'][0]['id']
    def test_registry_and_cloud_not_run_persistence(self):
        import time
        reg=self.client.get('/api/model-registry').json();self.assertIn('gpt-6-luna',[m['id'] for m in reg['models']])
        with patch('openai.OpenAI',side_effect=AssertionError('No SDK construction')):
            b=self.client.post('/api/workflows',json={'workspace_id':self.w,'task':'Analyze','source_ids':[self.f],'models':{'analyst':'gpt-6-luna'},'objectives':[{'id':'x','requirement':'Analyze'}]}).json()
            for _ in range(100):
                result=self.client.get('/api/workflows/'+b['id']).json()
                if result['status']!='running':break
                time.sleep(.01)
        self.assertEqual(result['result']['status'],'NOT RUN');self.assertEqual(result['result']['api_requests'],0)
        self.assertEqual(self.client.get('/api/workflows?workspace_id='+self.w).json()[0]['id'],b['id'])
    def test_cross_workspace_source_and_artifact_escape_rejected(self):
        other=self.client.post('/api/workspaces',json={'name':'other'}).json()['id']
        self.assertEqual(self.client.post('/api/workflows',json={'workspace_id':other,'task':'Analyze','source_ids':[self.f],'models':{'analyst':'qwen3.8:27b'},'objectives':[{'id':'x','requirement':'Analyze'}]}).status_code,403)
    def test_partial_settings_do_not_reset_persisted_modes(self):
        self.client.put('/api/settings',json={'execution_mode':'multi','workflow_models':{'planner':'qwen3.8:27b','analyst':'qwen3.8:27b','reviewer':'qwen3.8:27b'},'confidential_mode':True})
        d=self.client.put('/api/settings',json={'theme':'light'}).json();self.assertEqual(d['execution_mode'],'multi');self.assertTrue(d['confidential_mode'])

class AdversarialV3Tests(unittest.TestCase):
    setUp=V3Tests.setUp
    sources=V3Tests.sources
    def test_duplicate_join_trap_cannot_pass_financial_oracle(self):
        sources=self.sources();w=Workspace(self.root/'badjoin');self.addCleanup(w.db.close)
        for i,p in enumerate(sources):w.load(p,'s'+str(i))
        tables=list(w.frames);orders,customers,fx=tables
        q=w.query(f'SELECT o.order_id, o.net_usd*.1 AS commission_usd, o.net_usd*.1 AS commission_local, o.net_usd*.125 AS scenario_usd, o.net_usd*.125 AS scenario_local, o.net_usd*.025 AS delta_usd FROM "{orders}" o JOIN "{customers}" c ON o.customer_id=c.customer_id')
        checks=verifier(sources)(w,{'status':'answered','answer':'All results correct'},[])
        self.assertEqual(next(x for x in checks if x['objective_id']=='calculation')['status'],'FAIL')
    def test_schema_cache_and_objective_record_cannot_invent_evidence(self):
        from local_analyst.v3 import configure_agent
        w=Workspace(self.root/'cache');self.addCleanup(w.db.close)
        w.load(self.sources()[1],'customers');b=Budget();p=BoundedProvider(Script(lambda n,h,t:done()),b,'analyst',[],8)
        a,_=configure_agent(w,'local',p,b,[Objective('original','original financial request')],[],[])
        x=a.functions['inspect_schema']();y=a.functions['inspect_schema']();self.assertEqual(x['evidence_id'],y['evidence_id'])
        self.assertEqual(a.functions['record_objective'](objective_id='original',evidence_refs=['fabricated'],status='COMPLETED')['status'],'error')
        validation=validate_outcomes(w,{'status':'answered'},[Objective('original','financial')],[])
        self.assertEqual(validation['status'],'BLOCKED')
    def test_invalid_validator_fails_closed(self):
        w=Workspace(self.root/'v');self.addCleanup(w.db.close)
        def broken(*args):raise ValueError('formula invalid')
        self.assertEqual(validate_outcomes(w,{'status':'answered'},[Objective('x','requirement')],[],broken)['status'],'FAILED')



class V3RecoveryTests(unittest.TestCase):
    setUp=V3HTTPTests.setUp
    def test_restart_preserves_completed_and_blocks_interrupted_workflows(self):
        from web_api.app import Manager
        saved=self.m.store.put('workflow',self.w,status='COMPLETED',result={'evidence_refs':['frozen_q'],'api_requests':0})
        pending=self.m.store.put('workflow',self.w,status='running')
        restarted=Manager(self.root/'state',[self.root])
        try:
            self.assertEqual(restarted.store.get('workflow',saved['id'])['result']['evidence_refs'],['frozen_q'])
            self.assertEqual(restarted.store.get('workflow',pending['id'])['status'],'BLOCKED')
            self.assertEqual(restarted.runtimes,{})
        finally:
            restarted.executor.shutdown(wait=True);restarted.store.db.close();restarted.spend.db.close()
    def test_frozen_hash_cannot_download_unrelated_path(self):
        import hashlib
        external=self.root/'outside.csv';external.write_text('id,amount\n1,1\n');digest=hashlib.sha256(external.read_bytes()).hexdigest()
        b=self.m.store.put('workflow',self.w,status='COMPLETED',result={'artifacts':[{'path':str(external),'sha256':digest,'kind':'csv'}]})
        self.assertEqual(self.client.get('/api/workflows/'+b['id']+'/artifacts/'+digest).status_code,403)

if __name__=='__main__':unittest.main()
