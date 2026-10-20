"""Offline-only release-contract refinements after frozen live observations."""
import unittest,tempfile,socket
from pathlib import Path
from unittest.mock import patch
from local_analyst.financial_schema import FINANCIAL_SPEC,conditional_spec
from local_analyst.v3 import Budget,BoundedProvider,execute,handoff,sql_check
from local_analyst.providers import ProviderBlocked,OllamaProvider
from test_gridline_v3 import Script,done,calls
class ReleaseTests(unittest.TestCase):
 def test_field_contract_and_business_approval_cannot_be_fabricated(self):
  self.assertIn('rule_ref',FINANCIAL_SPEC['required'])
  with self.assertRaisesRegex(ValueError,'rule_ref'):conditional_spec({'version':1})
  spec={'version':1,'rule_ref':'User rules','source_key':'id','parameters':{},'calculations':[],'totals':[],'approved_result':True,'coverage_complete':True}
  with self.assertRaisesRegex(ValueError,'cannot approve'):conditional_spec(spec)
 def test_tool_free_synthesis_rejects_hallucinated_tool_calls(self):
  p=BoundedProvider(Script(lambda n,h,t:calls(('query_sql',{'sql':'SELECT 1'}))),Budget(), 'analyst',[],1)
  with self.assertRaisesRegex(ProviderBlocked,'FINAL_SYNTHESIS'):p.complete('local',[],[])
 def test_timeouts_follow_remaining_wall_not_another_full_timeout(self):
  b=Budget(max_seconds=10);inner=OllamaProvider('http://127.0.0.1:11434/api/chat');p=BoundedProvider(inner,b,'analyst',[],8)
  with patch.object(inner,'complete',return_value=(done(),{})):
   p.complete('local',[],[{'tool':'x'}])
  self.assertLessEqual(inner.timeout,10);self.assertGreater(inner.timeout,0)
 def test_typed_role_handoffs_reject_missing_original_objectives(self):
  self.assertEqual(handoff('planner',{'objectives':[{'id':'other','steps':['invent']}]},['original'])['status'],'invalid_handoff')
  self.assertEqual(handoff('reviewer',{'status':'PASS','findings':[]},[])['status'],'COMPLETED')
 def test_explicit_role_ceiling_total_not_per_agent_budget(self):
  with tempfile.TemporaryDirectory() as t,patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK_ISOLATED')):
   alloc={'planner':1,'analyst':3,'reviewer':1}
   r=execute('one task',[],Path(t)/'ok',mode='multi',models={k:'local' for k in alloc},max_requests=6,role_allocations=alloc,
     provider_factory=lambda _:Script(lambda n,h,t:done('{"status":"PASS","findings":[]}')))
   self.assertEqual(r['budget']['requests'],3);self.assertEqual(r['role_allocations'],alloc)
   with self.assertRaisesRegex(ValueError,'allocation'):execute('task',[],Path(t)/'bad',mode='multi',models={k:'local' for k in alloc},max_requests=3,role_allocations=alloc,provider_factory=lambda _:None)
 def test_prior_luna_missing_columns_regression_is_generic(self):
  from local_analyst.workspace import Workspace
  import pandas as pd
  with tempfile.TemporaryDirectory() as t:
   w=Workspace(t);w.register(pd.DataFrame({'worked_shift':[True],'shift_worked_date':['2026-06-01']}),'app',{'kind':'fixture'})
   try:
    r=sql_check(w,'SELECT SUM(charge_rate-pay_rate) FROM app');self.assertEqual(r['status'],'error');self.assertIn('worked_shift',r['available_columns']['app'])
   finally:w.db.close()

 def test_row_identifier_two_is_not_duplicate_count_evidence(self):
  from examples.v3_benchmark import create,verifier
  from local_analyst.workspace import Workspace
  with tempfile.TemporaryDirectory() as t:
   sources=create(Path(t)/'sources');w=Workspace(Path(t)/'w')
   try:
    for i,p in enumerate(sources):w.load(p,str('s'+str(i)))
    events=[{'tool':'query_sql','result':{'columns':['order_id','customer_id','row_id'],'rows':[['o1','cA',2]]}}, {'tool':'query_sql','result':{'columns':['order_id','currency'],'rows':[['o5','JPY']]}}]
    checks=verifier(sources)(w,{'answer':'missing JPY hold'},events)
    self.assertEqual(next(x for x in checks if x['objective_id']=='exceptions')['status'],'FAIL')
   finally:w.db.close()

 def test_raw_tool_markup_is_not_executed_or_accepted_as_synthesis(self):
  p=BoundedProvider(Script(lambda n,h,t:done('<tool_call>{"name":"query_sql"}</tool_call>')),Budget(),'analyst',[],1)
  with self.assertRaisesRegex(ProviderBlocked,'UNPARSED_TOOL_CALL_MARKUP'):p.complete('local',[],[])
