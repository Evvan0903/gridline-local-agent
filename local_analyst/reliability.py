"""Small controller helpers: root-cause progress and lossless repeat references."""
import copy,hashlib,json

class RootCauseProgress:
    def __init__(self):self.roots={};self.seen=set();self.stale=0;self.previous_roots={};self.warning_turns={};self.observations=0
    def observe(self,name,args,result,turn=None):
        self.observations+=1
        turn=self.observations if turn is None else turn
        failed=result.get('status') in {'error','FAIL','BLOCKED','REQUIRE_REVIEW'}
        signature=json.dumps([name,args],sort_keys=True,default=str)
        if failed:
            errors=result.get('errors') or result.get('issues') or [result]
            roots={(name,e.get('error_code') or result.get('error_category','TOOL_ERROR'),e.get('json_path') or '$') for e in errors}
            previous=self.previous_roots.get(name,set())
            if previous-roots:
                for root in previous|roots:
                    self.roots.pop(root,None);self.warning_turns.pop(root,None)
                result['validation_progress']='At least one prior structured defect was removed; remaining defects still require correction'
            self.previous_roots[name]=roots
            for root in roots:self.roots[root]=self.roots.get(root,0)+1
            count=max(self.roots[r] for r in roots)
            repeated=[r for r in roots if self.roots[r]>=3]
            for root in repeated:self.warning_turns.setdefault(root,turn)
            exhausted=any(turn>self.warning_turns[r] for r in repeated)
            if count>=3:
                result['stagnation']={'category':'REPEATED_VALIDATION_ROOT_CAUSE','root_causes':[list(r) for r in sorted(roots)],'occurrences':count,'correction_opportunities_remaining':0 if exhausted else 1,'instruction':'Correct the exact structured validation paths; changing descriptions/values without removing this defect is not progress.'}
            if exhausted:return 'STALLED: repeated validation root cause after targeted diagnostic and one correction opportunity'
        elif signature not in self.seen and name not in {'inspect_schema','record_objective','set_task_plan','validate_sql'} and result.get('dataset_versions') and (result.get('query_id') or result.get('evidence_id') or result.get('status')=='exported'):
            # Newly executed queries, quality checks or artifacts constitute observed progress.
            self.roots.clear();self.warning_turns.clear()
        self.stale=self.stale+1 if signature in self.seen else 0
        self.seen.add(signature)
        if self.stale>=6 and not failed:return 'STALLED: six repeated actions without new arguments or evidence'
        return None

def compact_history(history):
    """Only replace exact repeated tool data; first copy and all durable turns remain."""
    out=copy.deepcopy(history);seen={};references=[]
    for i,msg in enumerate(out):
        if msg.get('role')!='tool':continue
        try:body=json.loads(msg['content'])
        except (ValueError,TypeError,KeyError):continue
        if not isinstance(body,dict):continue
        result=body.get('result')
        if not isinstance(result,dict):continue
        canonical=copy.deepcopy(result)
        for key in ('evidence_id','cached_evidence'):canonical.pop(key,None)
        serialized=json.dumps(canonical,sort_keys=True,default=str)
        if len(serialized)<600:continue
        digest=hashlib.sha256(serialized.encode()).hexdigest()
        if digest not in seen:seen[digest]=i;continue
        refs={k:result[k] for k in ('status','error','error_code','json_path','query_id','evidence_id','dataset_versions','result_digest','truncated') if k in result}
        refs.update(full_result_reused=True,earlier_history_index=seen[digest],payload_sha256=digest,
                    instruction='Exact payload remains at earlier_history_index. IDs/versions retained; full originals preserved in history/events.')
        msg['content']=json.dumps({**body,'result':refs},default=str);references.append({'history_index':i,'earlier_history_index':seen[digest],'payload_sha256':digest})
    return out,references
