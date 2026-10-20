"""Synthetic, real-tools HTTP integration; model responses are scripted, not live."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from fastapi.testclient import TestClient
from local_analyst.agent import AnalystAgent
from web_api.app import create_app, TERMINAL


def call(tool_name, **arguments):
    return {'role':'assistant','content':'','tool_calls':[{'function':{'name':tool_name,'arguments':arguments}}]}


def final(content):return {'role':'assistant','content':content}


class WebAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.allowed=self.root/'allowed';self.allowed.mkdir()
        self.responses=[]
        outer=self
        class ScriptedAgent(AnalystAgent):
            def ask(agent,*args,**kwargs):
                def respond(*a,**k):
                    item=outer.responses.pop(0)
                    if callable(item):item=item(agent)
                    response=Mock();response.json.return_value={'message':item};return response
                with patch('local_analyst.agent.requests.post',side_effect=respond):
                    return super().ask(*args,**kwargs)
        self.factory=ScriptedAgent
        self.connect()
        self.addCleanup(lambda:self.client.__exit__(None,None,None))
        self.workspace=self.client.post('/api/workspaces',json={'name':'Synthetic review'}).json()['id']

    def connect(self):
        self.app=create_app(self.root/'state',[self.allowed],self.factory)
        self.manager=self.app.state.manager
        self.manager.models=lambda:{'available':True,'models':[{'id':'scripted-local','provider':'ollama','local':True}],'cloud':{'implemented':False,'credentials_configured':False},'usage':{'supported':False,'api_cost_usd':None,'remaining_budget_usd':None}}
        self.client=TestClient(self.app,base_url="http://localhost");self.client.__enter__()
        b=self.client.get('/api/bootstrap').json();self.client.headers['x-csrf-token']=b['csrf_token']

    def restart(self):
        self.client.__exit__(None,None,None);self.connect()

    def upload(self,data=b'id,amount\n1,10.10\n2,20.20\n',name='unknown.csv'):
        r=self.client.post(f'/api/workspaces/{self.workspace}/upload',files={'file':(name,data)})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['tables'][0]['table_id']

    def conversation(self):
        return self.client.post(f'/api/workspaces/{self.workspace}/conversations',json={}).json()['id']

    def submit(self,c,question):
        r=self.client.post(f'/api/conversations/{c}/messages',json={'content':question,'model':'scripted-local'})
        self.assertEqual(r.status_code,202,r.text)
        return r.json()['id']

    def wait(self,run):
        end=time.monotonic()+5
        while time.monotonic()<end:
            value=self.client.get('/api/runs/'+run).json()
            if value['status'] in TERMINAL:return value
            time.sleep(.01)
        self.fail('Run did not reach a bounded terminal state')

    def test_csv_loading_workspace_selection_schema_and_bounded_preview(self):
        table=self.upload();other=self.client.post('/api/workspaces',json={'name':'Other'}).json()['id']
        self.assertEqual(self.client.get(f'/api/workspaces/{other}').json()['tables'],[])
        x=self.client.get(f'/api/workspaces/{self.workspace}/tables/{table}').json()
        self.assertEqual(x['schema']['row_count'],2);self.assertEqual(x['preview']['preview_limit'],20)
        self.assertIn('version_id',x['schema'])

    def test_xlsx_unfamiliar_upload(self):
        output=io.BytesIO();pd.DataFrame({'customer':['Z','Y'],'amount':[7,8]}).to_excel(output,index=False)
        table=self.upload(output.getvalue(),'new-workbook.xlsx')
        self.assertEqual(self.client.get(f'/api/workspaces/{self.workspace}').json()['tables'][0]['table_id'],table)

    def test_inventory_actual_events_and_sse_replay_order(self):
        self.upload();run=self.submit(self.conversation(),'List all registered tables and their row counts. Do not run SQL.')
        self.assertEqual(self.wait(run)['status'],'completed')
        events=self.manager.store.events(run);kinds=[e['kind'] for e in events]
        self.assertNotIn('model_request_started',kinds)
        self.assertLess(kinds.index('tool_call_started'),kinds.index('tool_result_received'))
        self.assertEqual(kinds[-1],'run_completed')
        self.assertEqual([e['seq'] for e in events],list(range(1,len(events)+1)))
        text=self.client.get(f'/api/runs/{run}/events',headers={'last-event-id':'2'}).text
        self.assertNotIn('id: 1\n',text);self.assertIn('event: run_event',text);self.assertIn('run_completed',text)

    def export(self,derived=False):
        table=self.upload();c=self.conversation()
        if derived:
            runtime=self.manager.runtime(self.workspace);w=runtime.workspace
            w.register(w.frames[table].copy(),'derived_probe',{'kind':'derived','parents':{table:w.catalog[table]['version_id']}})
            sql='SELECT SUM(amount) AS total FROM derived_probe'
        else:sql=f'SELECT SUM(amount) AS total FROM {table}'
        self.responses=[call('query_sql',sql=sql),lambda agent:call('export_result',query_id=agent._query_ids[-1],name='verified_total',formats=['csv']),final('The total is 30.30.')]
        run=self.submit(c,'Compute total amount with SQL and export the result to CSV.')
        self.assertEqual(self.wait(run)['status'],'completed')
        return c,run,self.client.get(f'/api/workspaces/{self.workspace}/artifacts').json()[0]

    def test_actual_sql_evidence_and_authorized_export_download(self):
        c,run,artifact=self.export()
        e=self.client.get(f'/api/workspaces/{self.workspace}/evidence/{artifact["query_id"]}')
        self.assertEqual(e.status_code,200);self.assertEqual(e.json()['result']['rows'],[['30.30']])
        response=self.client.get(f'/api/artifacts/{artifact["id"]}/download')
        self.assertEqual(response.status_code,200);self.assertIn('30.30',response.text)
        events=self.manager.store.events(run);start=next(e for e in events if e['kind']=='tool_call_started')
        executed=next(e for e in events if e['kind']=='sql_executed')
        self.assertEqual(start['payload']['call_id'],executed['payload']['call_id'])
        self.assertTrue(executed['payload']['dataset_versions'])
        self.assertEqual(len(self.client.get('/api/conversations/'+c).json()['messages']),2)

    def test_restart_restores_raw_and_messages_but_not_derived(self):
        c,run,artifact=self.export(derived=True);self.restart()
        data=self.client.get(f'/api/workspaces/{self.workspace}').json()
        self.assertFalse(data['recovery']['derived_state_restored'])
        self.assertNotIn('derived_probe',[t['table_id'] for t in data['tables']])
        self.assertEqual(len(self.client.get('/api/conversations/'+c).json()['messages']),2)
        self.assertEqual(self.client.get(f'/api/artifacts/{artifact["id"]}/download').status_code,409)

    def test_raw_export_download_remains_valid_after_restart(self):
        _,_,artifact=self.export();self.restart()
        self.assertEqual(self.client.get(f'/api/artifacts/{artifact["id"]}/download').status_code,200)

    def test_artifact_tamper_and_source_change_rejected(self):
        _,_,artifact=self.export();saved=self.manager.store.get('artifact',artifact['id']);Path(saved['path']).write_text('tampered')
        self.assertEqual(self.client.get(f'/api/artifacts/{artifact["id"]}/download').status_code,409)

    def test_cooperative_cancellation_no_tool_starts_after_acknowledgement(self):
        table=self.upload();entered=threading.Event();release=threading.Event()
        def response(agent):entered.set();release.wait(3);return call('query_sql',sql=f'SELECT COUNT(*) FROM {table}')
        self.responses=[response];run=self.submit(self.conversation(),'Execute SQL to count records.')
        self.assertTrue(entered.wait(2));r=self.client.post(f'/api/runs/{run}/cancel')
        self.assertEqual(r.json()['status'],'cancelling');release.set()
        self.assertEqual(self.wait(run)['status'],'cancelled')
        events=self.manager.store.events(run)
        self.assertFalse(any(e['kind']=='tool_call_started' for e in events))
        self.assertEqual(events[-1]['kind'],'run_cancelled')
        self.assertEqual(self.client.post(f'/api/runs/{run}/cancel').json()['status'],'cancelled')

    def test_only_one_active_run_and_model_switch(self):
        self.upload();entered=threading.Event();release=threading.Event()
        def response(agent):entered.set();release.wait(3);return final('Unable to calculate without the required rule.')
        self.responses=[response];c=self.conversation();run=self.submit(c,'Calculate commission under missing rules.')
        self.assertTrue(entered.wait(2))
        self.assertEqual(self.client.post(f'/api/conversations/{c}/messages',json={'content':'other','model':'scripted-local'}).status_code,409)
        release.set();self.wait(run)
        self.manager.models=lambda:{'models':[{'id':'second-local'}]}
        self.responses=[final('Unable to calculate without the required rule.')]
        r=self.client.post(f'/api/conversations/{c}/messages',json={'content':'Calculate commission with no rules.','model':'second-local'}).json()
        self.assertEqual(self.wait(r['id'])['model'],'second-local')

    def test_wrong_origin_missing_session_csrf_and_host_rejected(self):
        self.assertEqual(self.client.get('/api/workspaces',headers={'origin':'https://untrusted.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/bootstrap',headers={'host':'rebind.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/workspaces',headers={'x-csrf-token':''},json={'name':'no'}).status_code,403)
        self.client.cookies.clear();self.assertEqual(self.client.get('/api/workspaces').status_code,401)

    def test_arbitrary_path_traversal_and_env_access_denied(self):
        outside=self.root/'private.csv';outside.write_text('id\n9\n')
        (self.allowed/'escape.csv').symlink_to(outside)
        for value in [str(outside),str(self.allowed/'escape.csv'),str(self.allowed/'../private.csv'),str(self.root/'.env')]:
            r=self.client.post(f'/api/workspaces/{self.workspace}/load',json={'path':value});self.assertIn(r.status_code,(400,403))
        self.assertEqual(self.client.post(f'/api/workspaces/{self.workspace}/upload',files={'file':('../escape.csv',b'a\n1')}).status_code,400)
        self.assertEqual(self.client.get('/api/artifacts/not-an-artifact/download').status_code,404)

    def test_agent_file_and_rule_tools_are_confined(self):
        self.upload();runtime=self.manager.runtime(self.workspace);agent=AnalystAgent(runtime.workspace)
        self.manager.guard_agent(agent,runtime)
        for tool in ['inspect_rules','load_data']:
            with self.assertRaises(Exception):agent.functions[tool](str(Path(__file__).parent/'.env'))

    def test_missing_credentials_never_enables_cloud_or_spend(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}),patch('web_api.app.requests.get',side_effect=RuntimeError('should use own discovery')):
            c=self.conversation();r=self.client.post(f'/api/conversations/{c}/messages',json={'content':'hi','provider':'openai','model':'gpt-6-luna'})
            self.assertEqual(r.status_code,403)
        self.assertIsNone(self.client.get('/api/providers').json()['usage']['api_cost_usd'])

    def test_credential_redaction_in_error_and_persistence(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'dummy-credential-for-boundary-test'}):
            c=self.conversation();self.responses=[final('Unable to calculate dummy-credential-for-boundary-test.')]
            self.upload();run=self.submit(c,'Calculate commission with missing rules.')
            self.wait(run);body=self.client.get('/api/conversations/'+c).text
            self.assertNotIn('dummy-credential-for-boundary-test',body)
            self.assertNotIn(b'dummy-credential-for-boundary-test',(self.root/'state/sessions.sqlite3').read_bytes())

    def test_settings_persist_and_error_outcomes_not_completed(self):
        self.client.put('/api/settings',json={'theme':'light','sidebar_width':280,'inspector_width':400})
        self.upload();self.responses=[final('I will calculate it next. Would you like me to proceed?')]
        run=self.submit(self.conversation(),'Calculate total amount.')
        self.assertEqual(self.wait(run)['status'],'incomplete')
        self.restart();self.assertEqual(self.client.get('/api/bootstrap').json()['settings'][0]['theme'],'light')

    def test_interrupted_run_recovered_as_incomplete(self):
        c=self.conversation();r=self.manager.store.put('run',self.workspace,workspace_id=self.workspace,conversation_id=c,model='scripted-local',status='running',result=None)
        self.restart();self.assertEqual(self.client.get('/api/runs/'+r['id']).json()['status'],'incomplete')
        self.assertEqual(self.manager.store.events(r['id'])[-1]['kind'],'run_incomplete')

    def test_cloud_network_is_never_used_for_discovery(self):
        self.manager._models=None
        response=Mock();response.json.return_value={'models':[{'name':'local-tag','capabilities':['tools']}]}
        with patch('web_api.app.requests.get',return_value=response) as get,patch('web_api.app.requests.post') as post:
            result=type(self.manager).models(self.manager)
        self.assertEqual(result['models'][0]['id'],'local-tag');get.assert_called_once_with('http://127.0.0.1:11434/api/tags',timeout=3);post.assert_not_called()
        self.assertTrue(result['cloud']['implemented'])

    def test_exact_decimal_and_large_integer_survive_http(self):
        table=self.upload(b'id,amount\n9007199254740993,10000000000000000.01\n')
        result=self.client.get(f'/api/workspaces/{self.workspace}/tables/{table}').json()['preview']
        values=dict(zip(result['columns'],result['rows'][0]))
        self.assertEqual(values['id'],'9007199254740993')
        self.assertEqual(values['amount'],'10000000000000000.01')

    def test_workspace_can_be_reopened_while_running(self):
        self.upload();entered=threading.Event();release=threading.Event()
        def response(agent):entered.set();release.wait(3);return final('Unable to calculate without approved rules.')
        self.responses=[response];run=self.submit(self.conversation(),'Calculate commission with missing rules.')
        self.assertTrue(entered.wait(2))
        data=self.client.get(f'/api/workspaces/{self.workspace}')
        self.assertEqual(data.status_code,200);self.assertEqual(data.json()['active_run']['id'],run)
        release.set();self.wait(run)

    def test_changed_source_blocks_existing_export(self):
        _,_,artifact=self.export()
        source=self.manager.store.list('file',self.workspace)[0]
        Path(source['path']).write_text('id,amount\n1,999\n')
        self.assertEqual(self.client.get(f'/api/artifacts/{artifact["id"]}/download').status_code,409)

    def test_blocked_plan_and_failed_runtime_are_distinct(self):
        self.upload();c=self.conversation()
        plan={'assumptions':[{'status':'unresolved','description':'No approved currency assumption'}],'steps':[]}
        r=self.client.post(f'/api/workspaces/{self.workspace}/upload',data={'reviewed_plan':'true'},files={'file':('reviewed.json',json.dumps(plan).encode())})
        self.assertEqual(r.status_code,200)
        self.responses=[call('run_model',plan_id=r.json()['plan_id']),final('Cannot calculate because the plan has unresolved assumptions.')]
        self.assertEqual(self.wait(self.submit(c,'Calculate total commission using the registered plan.'))['status'],'blocked')
        def failure(agent):raise RuntimeError('Internal failure must not be presented as completed work')
        self.responses=[failure]
        self.assertEqual(self.wait(self.submit(self.conversation(),'Compute the total amount.'))['status'],'failed')

    def test_file_size_and_invalid_workbook_rejected(self):
        with patch('web_api.app.MAX_FILE',10):
            r=self.client.post(f'/api/workspaces/{self.workspace}/upload',files={'file':('oversized.csv',b'a\n12345678901234')})
            self.assertEqual(r.status_code,413)
        r=self.client.post(f'/api/workspaces/{self.workspace}/upload',files={'file':('bad.xlsx',b'not an archive')})
        self.assertEqual(r.status_code,400)

    def test_hosted_ollama_tags_are_not_treated_as_local_models(self):
        response=Mock();response.json.return_value={'models':[
            {'name':'hosted:cloud','capabilities':['tools']},
            {'name':'remote-alias','capabilities':['tools'],'remote_model':'hosted','remote_host':'https://remote.invalid'},
            {'name':'local-weights','capabilities':['tools']}]}
        with patch('web_api.app.requests.get',return_value=response),patch('web_api.app.requests.post') as post:
            result=type(self.manager).models(self.manager)
        self.assertEqual([m['id'] for m in result['models'] if m['local']],['local-weights']);post.assert_not_called()


if __name__=='__main__':unittest.main()
