"""Explicit once-only local evaluation entry. No OpenAI key/network/provider path."""
import os,socket,json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from local_analyst.providers import OpenAIProvider
from .v3_benchmark import run_suite

def main():
    root=Path(__file__).resolve().parents[1]/'output/gridline_v3/local_evaluation'
    if root.exists():raise ValueError('Local evaluation already exists; preserve traces, do not automatically repeat')
    os.environ.pop('OPENAI_API_KEY',None)
    original_connect=socket.socket.connect;original_resolve=socket.getaddrinfo
    blocked=[]
    def connect(sock,address):
        if not isinstance(address,tuple) or address[0] not in {'127.0.0.1','::1','localhost'} or address[1]!=11434:
            blocked.append('non-Ollama connection');raise OSError('ONLY_LOCAL_OLLAMA_PERMITTED')
        return original_connect(sock,address)
    def resolve(host,*args,**kwargs):
        if host not in {'localhost','127.0.0.1','::1'}:
            blocked.append('non-loopback resolution');raise OSError('NON_LOOPBACK_DISABLED')
        return original_resolve(host,*args,**kwargs)
    with patch.object(socket.socket,'connect',connect),patch.object(socket,'getaddrinfo',resolve),patch.object(OpenAIProvider,'complete',side_effect=AssertionError('CLOUD_INFERENCE_FORBIDDEN')):
        try:results=run_suite(root)
        finally:
            root.mkdir(parents=True,exist_ok=True)
            (root/'network_audit.json').write_text(json.dumps({'allowed':'loopback Ollama port11434 only','OPENAI_API_KEY_removed_from_child_environment':True,'OpenAIProvider_complete_disabled':True,'blocked_attempts':blocked,'paid_api_requests':0,'cloud_spend_usd':'0'},indent=2))
    print(json.dumps({k:{'status':v['status'],'seconds':v.get('seconds'),'budget':v.get('budget')} for k,v in results.items()},indent=2))
if __name__=='__main__':main()
