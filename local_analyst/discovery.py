"""Read-only loopback Ollama discovery; no model pulling or cloud tags."""
import requests

def local_models():
    response=requests.get('http://127.0.0.1:11434/api/tags',timeout=5);response.raise_for_status()
    found={}
    for item in response.json().get('models',[])[:30]:
        tag=item.get('name','')
        if not tag or item.get('remote_model') or item.get('remote_host') or 'cloud' in tag.lower():continue
        r=requests.post('http://127.0.0.1:11434/api/show',json={'model':tag},timeout=5);r.raise_for_status();data=r.json()
        if data.get('remote_model') or data.get('remote_host') or 'tools' not in data.get('capabilities',[]):continue
        found[tag]={'id':tag,'provider':'ollama','local':True,'capabilities':data.get('capabilities'),
             'native_context_candidates':{k:v for k,v in data.get('model_info',{}).items() if k.endswith('.context_length')}}
    return found
