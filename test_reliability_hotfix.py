"""Offline regressions from recorded failures; no live model/API requests."""
import copy,json,tempfile,unittest,socket
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from local_analyst.financial_validation import FinancialPlanError,validate_financial_spec
from local_analyst.financial_model import evaluate,build_financial_model
from local_analyst.financial_eligibility import validate_financial_rows
from local_analyst.reliability import RootCauseProgress,compact_history
from local_analyst.v3 import Budget,BoundedProvider,execute,EXTENDED_LOCAL,configure_agent,Objective
from local_analyst.providers import ProviderBlocked,OllamaProvider
from local_analyst.workspace import Workspace
from test_gridline_v3 import Script,done
FIXTURE=Path(__file__).parent/'fixtures/reliability_hotfix/recorded_financial_failures.json'

def spec():return {'version':1,'rule_ref':'Explicit test rule','source_key':'id','parameters':{'rate':{'value':'.25'}},'calculations':[{'id':'fee','expression':{'mul':[{'field':'amount'},{'param':'rate'}]}}],'totals':['fee']}
class HotfixTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('OFFLINE_TEST_NETWORK_FORBIDDEN'));self.guard.start();self.addCleanup(self.guard.stop)
 def test_eight_recorded_arguments_prevalidated_without_compilation_or_rewrite(self):
  f=json.loads(FIXTURE.read_text());original=json.loads(Path('output/gridline_v3/qwen38_27b_extended_20261018/define_financial_plan_attempts.json').read_text());self.assertEqual(f['attempts'],[x['arguments'] for x in original])
  diagnostics=[]
  for i,a in enumerate(f['attempts']):
   frame=pd.DataFrame(f['query_results'][a['query_id']]['rows'],columns=f['query_results'][a['query_id']]['columns']);before=copy.deepcopy(a)
   with self.subTest(attempt=i+1),patch('local_analyst.financial_model.export_excel',side_effect=AssertionError('Compiler must not run')):
    with self.assertRaises(FinancialPlanError) as caught:build_financial_model(a['spec'],frame,self.root/str(i),{})
    errors=caught.exception.errors;diagnostics.append(errors)
    self.assertTrue(all('json_path' in e and 'expected_type' in e and 'received_type' in e for e in errors));self.assertEqual(a,before)
    self.assertEqual(errors[0]['error_code'],'DUPLICATE_CALCULATION_ID' if i<6 else 'BARE_REFERENCE')
    self.assertNotIn('ConversionSyntax',str(caught.exception))
  self.assertEqual(diagnostics[0][0]['json_path'],'$.calculations[0].id');self.assertEqual(diagnostics[0][0]['namespace'],'input_field')
 def test_duplicate_and_bare_string_guidance(self):
  frame=pd.DataFrame({'id':['x'],'amount':[10]});s=spec();s['calculations'][0]['id']='amount'
  with self.assertRaises(FinancialPlanError) as c:validate_financial_spec(s,frame)
  x=c.exception.errors[0];self.assertEqual(x['conflicting_identifier'],'amount');self.assertEqual(x['corrected_syntax_example']['id'],'calc_amount')
  s=spec();s['calculations'][0]['expression']={'mul':['amount','rate']}
  with self.assertRaises(FinancialPlanError) as c:validate_financial_spec(s,frame)
  self.assertEqual(c.exception.errors[0]['json_path'],'$.calculations[0].expression.mul[0]');self.assertEqual(c.exception.errors[1]['corrected_syntax_example'],{'param':'rate'})
 def test_parameter_declarations_require_numeric_constants(self):
  s=spec();s['parameters']['rate']['value']='amount'
  with self.assertRaises(FinancialPlanError) as caught:validate_financial_spec(s,pd.DataFrame({'id':['x'],'amount':[2]}))
  e=caught.exception.errors[0];self.assertEqual(e['json_path'],'$.parameters.rate.value');self.assertEqual(e['error_code'],'NUMERIC_LITERAL');self.assertNotIn('corrected_syntax_example',e)
 def test_valid_explicit_numeric_constants_and_references(self):
  self.assertEqual(str(evaluate(spec(),pd.DataFrame({'id':['x'],'amount':[12]})).fee.iloc[0]),'3.00')
 def test_missing_unknown_forward_and_unsupported_paths(self):
  f=pd.DataFrame({'id':['x'],'amount':[10]})
  for expr,code in [({'field':'later'},'FORWARD_REFERENCE'),({'param':'absent'},'UNKNOWN_REFERENCE'),({'formula':'SUM(A1)'},'UNSUPPORTED_FORMULA')]:
   s=spec();s['calculations'][0]['expression']=expr;s['calculations'].append({'id':'later','expression':2})
   with self.assertRaises(FinancialPlanError) as c:validate_financial_spec(s,f)
   self.assertIn(code,[e['error_code'] for e in c.exception.errors])
  s=spec();del s['parameters']
  with self.assertRaises(FinancialPlanError) as c:validate_financial_spec(s,f)
  self.assertEqual(c.exception.errors[0]['json_path'],'$.parameters')
 def workspace(self,unknown=False):
  p=self.root/'records.csv';p.write_text('id,amount,state,currency,rate\nx,10,ready,AUD,1.3\ny,20,ready,CAD,1.4\nz,30,hold,JPY,2\nd,40,dispute,GBP,3\n'+('u,9,unmapped,EUR,1\n' if unknown else ''))
  w=Workspace(self.root/'w');self.addCleanup(w.db.close);w.load(p);t=next(iter(w.catalog))
  c={'id':'eligibility','kind':'financial_rowset','rule_ref':'Reviewed status map','source_name':'records.csv','source_key':'id','status_column':'state','status_map':{'ready':'payable','hold':'held','dispute':'disputed'},'require_payable_only':True,'monetary_columns':{'amount':{'currency_column':'currency'}},'totals':[]}
  return w,t,c
 def test_held_disputed_excluded_and_audit_preserved(self):
  w,t,c=self.workspace();q=w.query(f'SELECT * FROM "{t}"')['query_id'];r=validate_financial_rows(w,q,c);self.assertEqual(r['status'],'FAIL');self.assertIn('INELIGIBLE_PAYABLE_ROWS',[x['error_code'] for x in r['issues']])
  q=w.query(f'SELECT * FROM "{t}" WHERE state=\'ready\'')['query_id'];r=validate_financial_rows(w,q,c);self.assertEqual(r['status'],'PASS');audit=json.loads(Path(r['audit']['path']).read_text());self.assertEqual(audit['partitions']['held'][0]['id'],'z');self.assertEqual(audit['partitions']['disputed'][0]['id'],'d');self.assertEqual(r['numerical_correctness'],'NOT_EVALUATED')
 def test_mixed_currency_global_total_rejected_per_currency_allowed(self):
  w,t,c=self.workspace();q=w.query(f'SELECT * FROM "{t}" WHERE state=\'ready\'')['query_id'];c['totals']=['amount'];r=validate_financial_rows(w,q,c);self.assertEqual(r['error_code'],'MIXED_CURRENCY_TOTAL')
  c['monetary_columns']['amount']['aggregation']='per_currency';r=validate_financial_rows(w,q,c);self.assertEqual(r['status'],'PASS');self.assertEqual(r['per_currency_subtotals']['amount'],{'AUD':'10','CAD':'20'})
  self.assertEqual(validate_financial_rows(w,q,c,totals=['amount'])['status'],'FAIL') # compiler global SUM is still forbidden
 def test_unknown_rules_and_source_status_block(self):
  w,t,c=self.workspace(True);q=w.query(f'SELECT * FROM "{t}" WHERE state=\'ready\'')['query_id'];self.assertEqual(validate_financial_rows(w,q)['status'],'BLOCKED');self.assertEqual(validate_financial_rows(w,q,c)['status'],'BLOCKED')
 def test_duplicate_join_coverage_and_currency_relabeling_rejected(self):
  w,t,c=self.workspace()
  for sql,code in [(f'SELECT * FROM "{t}" WHERE id=\'x\'','INELIGIBLE_PAYABLE_ROWS'),(f'SELECT * FROM "{t}" WHERE state=\'ready\' UNION ALL SELECT * FROM "{t}" WHERE id=\'x\'','DUPLICATE_RESULT_GRAIN'),(f'SELECT id,amount,state,\'USD\' AS currency,rate FROM "{t}" WHERE state=\'ready\'','CURRENCY_LINEAGE_MISMATCH')]:
   with self.subTest(code=code):r=validate_financial_rows(w,w.query(sql)['query_id'],c);self.assertIn(code,[x['error_code'] for x in r['issues']])
 def test_explicit_conversion_verified_against_source_rate(self):
  w,t,c=self.workspace();q=w.query(f'SELECT id,amount*rate AS converted,\'USD\' AS report_currency FROM "{t}" WHERE state=\'ready\'')['query_id'];c['monetary_columns']={'converted':{'currency_column':'report_currency','conversion':{'rule_ref':'Reviewed multiply rate to USD','reporting_currency':'USD','source_amount_column':'amount','source_currency_column':'currency','rate_column':'rate','operation':'multiply'}}};c['totals']=['converted'];self.assertEqual(validate_financial_rows(w,q,c)['status'],'PASS')
  q=w.query(f'SELECT id,amount*rate+1 AS converted,\'USD\' AS report_currency FROM "{t}" WHERE state=\'ready\'')['query_id'];self.assertEqual(validate_financial_rows(w,q,c)['error_code'],'CONVERSION_MISMATCH')
 def test_root_variants_get_one_targeted_opportunity_then_stop(self):
  p=RootCauseProgress()
  for i in range(4):
   r={'status':'error','errors':[{'error_code':'DUPLICATE_CALCULATION_ID','json_path':'$.calculations[0].id'}]};stop=p.observe('define',{'different_text':str(i)},r)
   if i==2:self.assertEqual(r['stagnation']['correction_opportunities_remaining'],1);self.assertIsNone(stop)
  self.assertIn('STALLED',stop)
 def test_batched_failures_allow_a_real_next_turn_correction(self):
  p=RootCauseProgress()
  for i in range(8):
   r={'status':'error','error_code':'DUPLICATE','json_path':'$.calculations[0].id'}
   self.assertIsNone(p.observe('define',{'text':str(i)},r,turn=1))
  self.assertEqual(r['stagnation']['correction_opportunities_remaining'],1)
  self.assertIn('STALLED',p.observe('define',{'text':'still broken'},{'status':'error','error_code':'DUPLICATE','json_path':'$.calculations[0].id'},turn=2))
 def test_new_evidence_and_changed_error_root_are_progress(self):
  p=RootCauseProgress()
  for i in range(12):
   e={'status':'error','errors':[{'error_code':'REFERENCE','json_path':'$.calculations[0].expression'}]};self.assertIsNone(p.observe('define',{'n':i},e));self.assertIsNone(p.observe('query_sql',{'sql':f'SELECT {i}'},{'status':'validated_current','query_id':f'q{i}','dataset_versions':{'source':'v1'}}))
 def test_source_free_probes_do_not_hide_unresolved_dsl_failure(self):
  p=RootCauseProgress()
  for i in range(3):
   p.observe('define',{'n':i},{'status':'error','error_code':'DUPLICATE','json_path':'$.x'})
   p.observe('validate_sql',{'sql':f'SELECT {i}'},{'status':'validated','dataset_versions':{}})
  self.assertIn('STALLED',p.observe('define',{'n':4},{'status':'error','error_code':'DUPLICATE','json_path':'$.x'}))
 def test_removing_a_defect_grants_fresh_correction_opportunity(self):
  p=RootCauseProgress()
  for i in range(3):p.observe('define',{'n':i},{'status':'error','errors':[{'error_code':'DUPLICATE','json_path':'$.a'},{'error_code':'BARE','json_path':'$.b'}]})
  r={'status':'error','errors':[{'error_code':'BARE','json_path':'$.b'}]};self.assertIsNone(p.observe('define',{'n':4},r));self.assertIn('validation_progress',r);self.assertNotIn('stagnation',r)
 def test_schema_hits_do_not_erase_root_cause(self):
  p=RootCauseProgress()
  for i in range(3):
   r={'status':'error','error_code':'DUPLICATE','json_path':'$.x'};p.observe('define',{'n':i},r);p.observe('inspect_schema',{}, {'status':'observed'})
  self.assertIn('stagnation',r)
 def test_lossless_dedup_preserves_versions_ids_objectives_and_assumptions(self):
  r={'rows':[['long'*400]],'query_id':'q1','evidence_id':'e1','dataset_versions':{'t':'v1'},'status':'validated_current'}
  h=[{'role':'system','content':'original objectives and governing assumptions'},{'role':'tool','content':json.dumps({'ok':True,'result':r})},{'role':'tool','content':json.dumps({'ok':True,'result':{**r,'evidence_id':'e2'}})}];before=copy.deepcopy(h);out,refs=compact_history(h)
  self.assertEqual(h,before);self.assertEqual(len(refs),1);x=json.loads(out[2]['content'])['result'];self.assertEqual(x['query_id'],'q1');self.assertEqual(x['dataset_versions'],{'t':'v1'});self.assertEqual(x['evidence_id'],'e2');self.assertIn('long',out[x['earlier_history_index']]['content']);self.assertEqual(out[0],h[0])
  h[2]['content']=json.dumps({'result':{**r,'dataset_versions':{'t':'v2'}}});self.assertFalse(compact_history(h)[1])
 def test_actual_recorded_history_reuses_errors_without_losing_durable_turns(self):
  h=json.loads(Path('output/gridline_v3/qwen38_27b_extended_20261018/run/history.json').read_text());before=copy.deepcopy(h);out,refs=compact_history(h);self.assertEqual(h,before);self.assertEqual(len(out),len(h));self.assertGreater(len(refs),0);self.assertLess(len(json.dumps(out)),len(json.dumps(h)))
  for r in refs:self.assertEqual(out[r['earlier_history_index']],h[r['earlier_history_index']])
 def test_historical_202_second_boundary_requests_no_tools(self):
  b=Budget(None,None,3600,request_timeout=600,extended=True);inner=OllamaProvider('http://127.0.0.1:11434/api/chat');p=BoundedProvider(inner,b,'analyst',[],None)
  with patch('local_analyst.v3.time.monotonic',return_value=b.start+3398),patch.object(inner,'complete',return_value=(done(),{})) as mock:
   p.complete('qwen3.8:27b',[],[{'tool':'x'}]);self.assertTrue(p.final_synthesis);self.assertEqual(mock.call_args.args[2],[]);self.assertEqual(inner.timeout,202)
 def test_worst_case_tool_request_leaves_conservative_synthesis_window(self):
  now=[2399.];b=Budget(None,None,3600,request_timeout=600,extended=True);b.start=0
  def reply(n,h,tools):
   if n==1:self.assertTrue(tools);now[0]+=600
   else:self.assertFalse(tools);now[0]+=590
   return done()
  p=BoundedProvider(Script(reply),b,'analyst',[],None)
  with patch('local_analyst.v3.time.monotonic',side_effect=lambda:now[0]):
   p.complete('qwen3.8:27b',[],[{'x':1}]);p.complete('qwen3.8:27b',[],[{'x':1}]);self.assertLess(now[0],3600)
   now[0]=3600
   with self.assertRaisesRegex(ProviderBlocked,'WALL_TIME'):p.complete('qwen3.8:27b',[],[])
 def test_partial_result_survives_synthesis_timeout_no_fake_deliverables(self):
  import requests
  r=execute('Create Excel and PDF',[],self.root/'run',models={'analyst':'qwen3.8:27b'},formats=['excel','pdf'],execution_profile=EXTENDED_LOCAL,provider_factory=lambda _:Script(lambda *a:(_ for _ in ()).throw(requests.ReadTimeout('simulated'))))
  part=r['structured_partial_result'];self.assertEqual(part['status'],'PARTIAL');self.assertEqual(part['artifacts'],[]);self.assertEqual(part['missing_formats'],['excel','pdf']);self.assertFalse(part['synthesis_received'])
 def test_completion_recognizes_uppercase_semantic_failures(self):
  from local_analyst.completion import check_completion
  w=Workspace(self.root/'completion');self.addCleanup(w.db.close)
  for status in ('FAIL','BLOCKED','REQUIRE_REVIEW'):
   with self.subTest(status=status):
    r=check_completion('Validate payable rows','All requested financial work completed.',[{'tool':'validate_financial_rows','result':{'status':status}}],w)
    self.assertNotEqual(r['status'],'answered');self.assertTrue(any('blocked' in reason or 'tool error' in reason.lower() for reason in r['reasons']))
 def test_valid_scripted_pipeline_live_excel_pdf_and_changed_scenario(self):
  from examples.reliability_hotfix_pipeline import run_pipeline
  r=run_pipeline(self.root/'scripted_pipeline');self.assertEqual(r['status'],'COMPLETED');self.assertEqual(r['real_model_requests'],0);self.assertTrue(r['independent']['dynamic_scenario_pass']);self.assertTrue(r['independent']['reconciliation_pass']);self.assertTrue(all(r['independent']['checks'].values()))
 def test_output_gate_blocks_ineligible_before_evaluation(self):
  w,t,c=self.workspace();q=w.query(f'SELECT * FROM "{t}"')['query_id'];b=Budget();provider=BoundedProvider(Script(lambda *a:done()),b,'analyst',[],8);agent,_=configure_agent(w,'local',provider,b,[Objective('one','eligibility')],[],['excel'],approved_rule_text='Explicit reviewed status rule',financial_contracts=[c])
  with patch('local_analyst.v3.evaluate',side_effect=AssertionError('Ineligible input must not be calculated')):
   r=agent.functions['define_financial_plan'](query_id=q,spec=spec())
  self.assertEqual(r['error_code'],'INELIGIBLE_PAYABLE_ROWS');self.assertFalse(w.plans)
 def test_schema_cache_revalidates_source_before_reuse(self):
  w,t,c=self.workspace();b=Budget();provider=BoundedProvider(Script(lambda *a:done()),b,'analyst',[],8);agent,_=configure_agent(w,'local',provider,b,[Objective('one','evidence')],[],[])
  self.assertFalse(agent.functions['inspect_schema'](table_id=t)['cached_evidence']);self.assertTrue(agent.functions['inspect_schema'](table_id=t)['cached_evidence'])
  (self.root/'records.csv').write_text('id,amount,state,currency,rate\nx,99,ready,AUD,1.3\n')
  r=agent.functions['inspect_schema'](table_id=t);self.assertEqual(r['status'],'error');self.assertFalse(r.get('cached_evidence',False))
 def test_contract_tool_schema_and_backend_verifier(self):
  from local_analyst.contracts import contract_verifier
  w,t,c=self.workspace();c['objective_id']='eligibility';q=w.query(f'SELECT * FROM "{t}" WHERE state=\'ready\'')['query_id'];events=[{'tool':'validate_financial_rows','arguments':{'contract_id':c['id']},'result':{'query_id':q}}]
  r=contract_verifier([c])(w,{},events)[0];self.assertEqual(r['status'],'PASS');self.assertEqual(r['scope'],'eligibility_and_currency_only')
 def test_synthesis_timeout_retains_executed_evidence_in_partial_record(self):
  import requests
  from test_gridline_v3 import calls
  source=self.root/'input.csv';source.write_text('id,amount\nx,20\n')
  def response(n,h,t):
   if n==1:return calls(('inspect_schema',{}))
   if n==2:
    result=next(json.loads(m['content'])['result'] for m in h if m.get('role')=='tool')
    return calls(('query_sql',{'sql':'SELECT * FROM "'+result['tables'][0]['table_id']+'"'}))
   raise requests.ReadTimeout('simulated final request timeout')
  r=execute('Generate dynamic Excel and PDF',[source],self.root/'partial',models={'analyst':'local'},formats=['excel','pdf'],max_requests=3,provider_factory=lambda _:Script(response))
  part=r['structured_partial_result'];self.assertEqual(part['missing_formats'],['excel','pdf']);self.assertEqual(r['budget']['requests'],3);self.assertEqual(len(part['verified_query_evidence']),1);self.assertEqual(part['verified_query_evidence'][0]['status'],'validated_current');self.assertTrue(part['synthesis_requested']);self.assertFalse(part['synthesis_received'])
if __name__=='__main__':unittest.main()
