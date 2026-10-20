"""Backend-owned model metadata. Unknown native limits/capabilities remain null."""
from .providers import CLOUD_MODELS,PRICING

def model_registry(installed=(), *, connected=False):
    installed={m['id']:m for m in installed if m.get('local',True)}
    ids=list(dict.fromkeys(['qwen3.8:27b','qwen3:8b',*installed,*CLOUD_MODELS]))
    result=[]
    for model in ids:
        cloud=model in CLOUD_MODELS
        result.append({'id':model,'label':model,'provider':'openai' if cloud else 'ollama','local':not cloud,
            'available':False if cloud else model in installed,'installed':None if cloud else model in installed,
            'supports_tools':True if cloud or model in installed else None,'structured_outputs':True if cloud else None,
            'native_context_limit':installed.get(model,{}).get('native_context_limit'),'native_output_limit':None,'configured_context':None if cloud else 16384,
            'configured_reasoning':{'effort':'low'} if cloud else {'think':False},
            'configured_max_output':4096 if cloud else None,'reasoning':['low','medium','high'] if cloud else [],
            'pricing':PRICING['models'].get(model),'authorization_required':cloud,
            'execution_state':'NOT RUN: V3 cloud execution disabled; new authorization/integration required' if cloud else ('connected' if model in installed and connected else 'not installed or disconnected'),
            'capability_evidence':'Official API docs; no V3 live cloud validation' if cloud else ('Ollama local discovery tools capability' if model in installed else 'unverified')})
    return result
