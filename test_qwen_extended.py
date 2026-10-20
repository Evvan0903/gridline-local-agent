"""No model/network calls: extended profile's boundaries and progress guards."""
import tempfile,unittest,time,socket
from pathlib import Path
from unittest.mock import patch
from local_analyst.v3 import Budget,BoundedProvider,configure_agent,execute,EXTENDED_LOCAL,Objective
from local_analyst.providers import OllamaProvider,ProviderBlocked
from local_analyst.workspace import Workspace
from test_gridline_v3 import Script,done,calls

class ExtendedTests(unittest.TestCase):
 def test_defaults_unchanged(self):
  b=Budget();self.assertEqual((b.max_requests,b.max_tools,b.max_seconds,b.request_timeout),(8,24,600,180))
  for _ in range(8):b.request()
  with self.assertRaises(ProviderBlocked):b.request()
  for _ in range(24):b.call()
  with self.assertRaises(ProviderBlocked):b.call()
 def test_unlimited_counts_and_deadline(self):
  b=Budget(None,None,3600,request_timeout=600,extended=True)
  for _ in range(100):b.request();b.call()
  self.assertEqual(b.snapshot()['requests'],100);self.assertIsNone(b.snapshot()['remaining_tools'])
  with patch('local_analyst.v3.time.monotonic',return_value=b.start+3600.01):
   with self.assertRaisesRegex(ProviderBlocked,'WALL_TIME'):b.request()
 def test_cancel(self):
  b=Budget(None,None,3600,cancelled=lambda:True)
  with self.assertRaisesRegex(ProviderBlocked,'CANCELLED'):b.call()
 def test_timeout_600_and_final_synthesis(self):
  b=Budget(None,None,3600,request_timeout=600,extended=True);inner=OllamaProvider('http://127.0.0.1:11434/api/chat');p=BoundedProvider(inner,b,'analyst',[],None)
  with patch.object(inner,'complete',return_value=(done(),{})):
   p.complete('qwen3.8:27b',[],[{'x':1}]);self.assertEqual(inner.timeout,600);self.assertFalse(p.final_synthesis)
   with patch('local_analyst.v3.time.monotonic',return_value=b.start+3500):
    p.complete('qwen3.8:27b',[],[{'x':1}]);self.assertEqual(inner.timeout,100);self.assertTrue(p.final_synthesis)
 def test_profile_exact_model_mode_only(self):
  for model in ('qwen3:8b','gpt-6-luna','gpt-6.1-sol','other'):
   with tempfile.TemporaryDirectory() as t,patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK_FORBIDDEN')):
    with self.assertRaisesRegex(ValueError,'exactly'):execute('task',[],Path(t)/'r',models={'analyst':model},execution_profile=EXTENDED_LOCAL)
 def test_more_than_8_requests_and_24_real_tools(self):
  with tempfile.TemporaryDirectory() as t:
   def response(n,h,tools):
    if n<=10:return calls(*[('validate_sql',{'sql':f'SELECT {n*3+i} AS value'}) for i in range(3)])
    return done('No financial approval or missing facts invented.')
   r=execute('inspect',[],Path(t)/'run',execution_profile=EXTENDED_LOCAL,provider_factory=lambda _:Script(response))
   self.assertEqual((r['budget']['requests'],r['budget']['tools']),(11,30));self.assertIsNone(r['role_allocations']['analyst'])
 def test_repeated_failure_stops_not_corrected_work(self):
  with tempfile.TemporaryDirectory() as t:
   r=execute('inspect',[],Path(t)/'run',execution_profile=EXTENDED_LOCAL,provider_factory=lambda _:Script(lambda n,h,t:calls(('validate_sql',{'sql':'SELECT missing FROM nowhere'}))))
   self.assertIn('STALLED',str(r['answer']));self.assertEqual(r['budget']['tools'],4)
 def test_repeated_success_stalls(self):
  with tempfile.TemporaryDirectory() as t:
   r=execute('inspect',[],Path(t)/'run',execution_profile=EXTENDED_LOCAL,provider_factory=lambda _:Script(lambda n,h,t:calls(('inspect_schema',{}))))
   self.assertIn('STALLED',str(r['answer']));self.assertEqual(r['budget']['tools'],7)
 def test_no_retry_after_request_timeout(self):
  import requests
  with tempfile.TemporaryDirectory() as t:
   p=Script(lambda *a:(_ for _ in ()).throw(requests.ReadTimeout('simulated 600 second request timeout')))
   r=execute('inspect',[],Path(t)/'run',execution_profile=EXTENDED_LOCAL,provider_factory=lambda _:p)
   self.assertEqual(len(p.requests),1);self.assertIn('timeout',r['answer']['error'])
 def test_cloud_block_before_inner(self):
  p=BoundedProvider(Script(lambda *a:(_ for _ in ()).throw(AssertionError('CLOUD'))),Budget(None,None,3600),'analyst',[],None)
  with self.assertRaisesRegex(ProviderBlocked,'CLOUD'):p.complete('gpt-6-luna',[],[])
if __name__=='__main__':unittest.main()
