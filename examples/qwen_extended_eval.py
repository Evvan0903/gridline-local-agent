"""One explicitly authorized local experiment; no retries, cloud or hidden answers."""
import os,json,socket,signal,threading,time,shutil,hashlib,subprocess
from pathlib import Path
from unittest.mock import patch
import requests
from local_analyst.providers import OpenAIProvider,OllamaProvider,ProviderBlocked
from local_analyst.v3 import execute,EXTENDED_LOCAL
from local_analyst.workspace import fingerprint
from local_analyst.discovery import local_models
from examples.v3_benchmark import TASK,RULES,OBJECTIVES
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/gridline_v3/qwen38_27b_extended_20261018'
MODEL='qwen3.8:27b'

def termination(result,events):
 reason=' '.join(result.get('answer',{}).get('completion',{}).get('reasons',[]))
 text=reason+' '+str(result.get('answer',{}).get('error',''))
 if result.get('status')=='COMPLETED':return 'COMPLETED'
 if 'WALL_TIME' in text:return 'TIME_LIMIT'
 if 'CANCEL' in text.upper():return 'CANCELLED'
 if 'STALLED' in text or 'repeated_errors_without_progress' in text:return 'STALLED'
 if any(e.get('category') in {'ReadTimeout','ConnectTimeout','Timeout'} for e in events) or 'timeout' in text.lower():return 'REQUEST_TIMEOUT'
 if any(e.get('kind')=='controller_error' for e in events):return 'UNRECOVERABLE_ERROR'
 if reason in {'local_context_limit','malformed_tool_response'} or 'UNPARSED_TOOL_CALL' in text:return 'UNRECOVERABLE_ERROR'
 return 'INCOMPLETE'

def main():
 if OUT.exists():raise ValueError('ONCE_ONLY: existing experiment preserved; no automatic repeat')
 OUT.mkdir(parents=True)
 os.environ.pop('OPENAI_API_KEY',None)
 old=ROOT/'output/gridline_v3/local_evaluation'
 sources=[]
 for name in ('Orders.xlsx','Customers.csv','FX.csv'):
  dest=OUT/'sources'/name;dest.parent.mkdir(exist_ok=True);shutil.copy2(old/'sources'/name,dest);sources.append(dest)
 manifest=json.loads((old/'evaluation_manifest.json').read_text())
 assert TASK==manifest['task'] and RULES==manifest['rules'] and OBJECTIVES==manifest['objectives']
 assert all(fingerprint(p)==next(h for k,h in manifest['source_sha256'].items() if Path(k).name==p.name) for p in sources)
 code=OUT/'code_snapshot';code.mkdir()
 hashes={}
 for folder in ('local_analyst','examples'):
  for p in (ROOT/folder).rglob('*'):
   if p.suffix not in {'.py','.mjs'} or 'node_modules' in p.parts:continue
   rel=p.relative_to(ROOT);target=code/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target);hashes[str(rel)]=fingerprint(p)
 frozen={'model':MODEL,'mode':'single','tool_profile':'general','execution_profile':EXTENDED_LOCAL,'max_seconds':3600,'per_request_seconds':600,'max_requests':None,'max_tools':None,'task':TASK,'rules':RULES,'objectives':OBJECTIVES,'sources':{str(p):fingerprint(p) for p in sources},'code_sha256':hashes,'oracle_available_to_model':False,'historical_task_eight_request_instruction':'Overridden only by explicit execution-profile system message','cloud_requests':0,'cloud_spend_usd':'0'}
 (OUT/'evaluation_manifest.json').write_text(json.dumps(frozen,indent=2))
 original_connect=socket.socket.connect;original_resolve=socket.getaddrinfo;blocked=[]
 def connect(sock,address):
  if not isinstance(address,tuple) or address[0] not in {'127.0.0.1','::1','localhost'} or address[1]!=11434:
   blocked.append('non-Ollama connection blocked');raise OSError('ONLY_LOCAL_OLLAMA_PERMITTED')
  return original_connect(sock,address)
 def resolve(host,*args,**kwargs):
  if host not in {'localhost','127.0.0.1','::1'}:
   blocked.append('non-loopback resolution blocked');raise OSError('NON_LOOPBACK_DISABLED')
  return original_resolve(host,*args,**kwargs)
 stop=threading.Event();cancelled=threading.Event();state={'phase':'preflight','initial_resident':[],'inference_started':False};deadline=None
 def alarm(signum,frame):
  if deadline and time.monotonic()>=deadline:raise ProviderBlocked('WALL_TIME_LIMIT')
  raise requests.ReadTimeout('REQUEST_TIMEOUT: authorized per-request deadline reached; no retry')
 def cancel(signum,frame):
  cancelled.set();raise ProviderBlocked('CANCELLED')
 def sample():
  while not stop.wait(1):
   if (OUT/'CANCEL').exists() and not cancelled.is_set():os.kill(os.getpid(),signal.SIGUSR1)
   if int(time.monotonic())%15:continue
   entry={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'phase':state['phase'],'loadavg':os.getloadavg()}
   try:
    entry['vm_stat']=subprocess.run(['vm_stat'],capture_output=True,text=True,timeout=3).stdout
    entry['ollama_ps']=requests.get('http://127.0.0.1:11434/api/ps',timeout=3).json()
   except Exception as e:entry['sample_error']=type(e).__name__
   with (OUT/'resources.jsonl').open('a') as f:f.write(json.dumps(entry)+'\n')
 class DeadlineOllama(OllamaProvider):
  def complete(self,model,history,tools):
   assert model==MODEL
   remaining=deadline-time.monotonic()
   if remaining<=0:raise ProviderBlocked('WALL_TIME_LIMIT')
   signal.setitimer(signal.ITIMER_REAL,min(self.timeout,remaining))
   try:return super().complete(model,history,tools)
   finally:signal.setitimer(signal.ITIMER_REAL,max(.001,deadline-time.monotonic()))
 signal.signal(signal.SIGALRM,alarm);signal.signal(signal.SIGUSR1,cancel);signal.signal(signal.SIGINT,cancel)
 def verify_after(w,answer,events):
  # No oracle execution or expected values before the model's terminal turn.
  signal.setitimer(signal.ITIMER_REAL,0);state['inference_seconds']=round(time.monotonic()-state['started_monotonic'],3);state['phase']='independent_verification'
  (OUT/'inference_stopped.json').write_text(json.dumps({'inference_seconds':state['inference_seconds'],'model_requests':sum(e.get('kind')=='model_request' for e in events),'tools':sum(e.get('kind') in {'tool','tool_error'} for e in events),'answer':answer},indent=2,default=str))
  from examples.v3_benchmark import verifier
  return verifier(sources)(w,answer,events)
 try:
  with patch.object(socket.socket,'connect',connect),patch.object(socket,'getaddrinfo',resolve),patch.object(OpenAIProvider,'complete',side_effect=AssertionError('CLOUD_INFERENCE_FORBIDDEN')):
   installed=local_models()
   if MODEL not in installed:raise ValueError('Exact requested model unavailable; no substitution/download')
   state['initial_resident']=requests.get('http://127.0.0.1:11434/api/ps',timeout=5).json().get('models',[])
   if any(m['name']!=MODEL for m in state['initial_resident']):raise ValueError('Other user model resident: no parallel heavyweight inference and no automatic eviction')
   (OUT/'preflight.json').write_text(json.dumps({'installed_target':installed[MODEL] if isinstance(installed,dict) else MODEL,'state':state,'cancellation':'Create CANCEL file here or SIGINT runner; in-flight SIGUSR1 interrupts request','paid_requests':0},indent=2,default=str))
   thread=threading.Thread(target=sample,daemon=True);thread.start()
   state.update(phase='inference',inference_started=True,started_monotonic=time.monotonic());deadline=state['started_monotonic']+3600
   signal.setitimer(signal.ITIMER_REAL,3600)
   result=execute(TASK,sources,OUT/'run',models={'analyst':MODEL},objectives=OBJECTIVES,rule_text=RULES,formats=['excel','pdf'],execution_profile=EXTENDED_LOCAL,provider_factory=lambda m:DeadlineOllama('http://127.0.0.1:11434/api/chat',600),verifier=verify_after,cancelled=cancelled.is_set)
   events=[json.loads(x) for x in (OUT/'run/events.jsonl').read_text().splitlines()]
   summary={'status':result['status'],'termination_reason':termination(result,events),'inference_seconds':state.get('inference_seconds'),'total_execute_seconds':result['seconds'],'actual_model_requests':sum(e.get('kind')=='model_request' for e in events),'executed_tool_attempts':sum(e.get('kind') in {'tool','tool_error'} for e in events),'successful_tools':sum(e.get('kind')=='tool' and e.get('result',{}).get('status')!='error' for e in events),'failed_tools':sum(e.get('kind')=='tool_error' or (e.get('kind')=='tool' and e.get('result',{}).get('status')=='error') for e in events),'reported_local_tokens':result['local_tokens'],'source_integrity':result['source_integrity'],'artifacts':result['artifacts'],'validation':result['validation'],'cloud_requests':0,'cloud_spend_usd':'0'}
   summary['exceeded_eight_requests']=summary['actual_model_requests']>8;summary['exceeded_24_tools']=summary['executed_tool_attempts']>24
   (OUT/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
 except Exception as e:
  (OUT/'runner_error.json').write_text(json.dumps({'category':type(e).__name__,'error':str(e),'state':state},indent=2));raise
 finally:
  signal.setitimer(signal.ITIMER_REAL,0);stop.set()
  if 'thread' in locals():thread.join(timeout=5)
  # Release only cache loaded by this experiment; never stop service or evict initial user caches.
  if state['inference_started'] and not state['initial_resident']:
   try:
    with patch.object(socket.socket,'connect',connect),patch.object(socket,'getaddrinfo',resolve):
     r=requests.post('http://127.0.0.1:11434/api/generate',json={'model':MODEL,'keep_alive':0},timeout=60);r.raise_for_status()
     remaining=requests.get('http://127.0.0.1:11434/api/ps',timeout=5).json()
    (OUT/'cleanup.json').write_text(json.dumps({'own_model_cache_released':True,'remaining':remaining,'service_preserved':True},indent=2))
   except Exception as e:(OUT/'cleanup.json').write_text(json.dumps({'error':type(e).__name__,'service_preserved':True}))
  (OUT/'network_audit.json').write_text(json.dumps({'allowed':'loopback11434 only','key_removed':True,'OpenAIProvider_disabled':True,'blocked_attempts':blocked,'cloud_requests':0,'cost_usd':'0'},indent=2))
if __name__=='__main__':main()
