"""Scripted Ollama messages, real workspace/SQL/export execution and actual value checks."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from local_analyst.agent import AnalystAgent, export_intent
from local_analyst.workspace import Workspace, fingerprint


def call(tool_name, **arguments):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": tool_name, "arguments": arguments}}]}


class AnalysisModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.w = Workspace(self.root / 'session')
        self.addCleanup(self.w.db.close)
        source = self.root / 'sales.csv'
        source.write_text('customer,month,revenue\nA,2026-06,150\nB,2026-06,80\nA,2026-05,999\n')
        self.source = source
        self.table = self.w.load(source, 'sales')['tables'][0]['table_id']
        self.agent = AnalystAgent(self.w)
        self.sql = f'SELECT SUM(revenue) AS revenue FROM "{self.table}" WHERE month=$month'

    def ask(self, question, messages, **options):
        payloads = []
        def respond(*args, **kwargs):
            payloads.append(json.loads(json.dumps(kwargs['json'])))
            item = messages.pop(0)
            message = item() if callable(item) else item
            response = Mock()
            response.json.return_value = {'message': message}
            return response
        with patch('local_analyst.agent.requests.post', side_effect=respond):
            result = self.agent.ask(question, **options)
        self.assertFalse(messages, 'Agent ended before executing the scripted workflow')
        return result, payloads

    def latest_id(self):
        return next(reversed(self.w.queries))

    def query(self):
        return call('query_sql', sql=self.sql, parameters={'month': '2026-06'})

    def final(self, answer='June revenue is 230; month is the supplied YYYY-MM column.'):
        return {'role': 'assistant', 'content': answer + ' Evidence: ' + self.latest_id()}

    def assert_no_artifacts(self):
        self.assertFalse((self.w.root / 'outputs').exists())
        self.assertFalse(list(self.w.root.rglob('*.csv')))
        self.assertFalse(list(self.w.root.rglob('*.xlsx')))
        self.assertFalse(list(self.w.root.rglob('*.pdf')))

    def test_direct_answer_and_follow_up_same_workspace(self):
        before = fingerprint(self.source)
        result, sent = self.ask('What was our total revenue in June?', [
            call('inspect_schema', table_id=self.table), self.query(), self.final])
        self.assertEqual(result['status'], 'answered')
        query = result['trace'][1]['result']
        self.assertEqual(query['rows'], [[230]])
        self.assertIn(query['query_id'], result['answer'])
        self.assertEqual(result['evidence'][0]['dataset_versions'], query['dataset_versions'])
        self.assertNotIn('export_result', [t['function']['name'] for t in sent[0]['tools']])
        self.assert_no_artifacts()
        follow, sent = self.ask('Which customers contributed the most in that month?', [
            call('query_sql', sql=f'SELECT customer,SUM(revenue) AS revenue FROM "{self.table}" WHERE month=$month GROUP BY customer ORDER BY revenue DESC', parameters={'month': '2026-06'}),
            lambda: self.final('A: 150; B: 80, restricted to June.')])
        self.assertEqual(follow['trace'][0]['result']['rows'], [['A', 150], ['B', 80]])
        self.assertEqual(follow['trace'][0]['result']['dataset_versions'], query['dataset_versions'])
        self.assertTrue(any(m.get('content') == 'What was our total revenue in June?' for m in sent[0]['messages']))
        self.assert_no_artifacts()
        self.assertEqual(fingerprint(self.source), before)

    def test_explicit_csv_export_and_permission_resets(self):
        self.ask('What was June revenue?', [self.query(), self.final])
        result, sent = self.ask('Export this result to CSV.', [
            lambda: call('inspect_query_evidence', query_id=self.latest_id()),
            lambda: call('export_result', query_id=self.latest_id(), name='june'),
            lambda: self.final('Exported June revenue 230 as CSV.')])
        output = result['trace'][1]['result']
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(output['status'], 'exported')
        self.assertEqual(pd.read_csv(output['csv']).revenue.tolist(), [230])
        self.assertNotIn('excel', output)
        self.assertNotIn('pdf', output)
        lineage = json.loads(Path(output['lineage']).read_text())
        self.assertEqual(lineage['query_id'], self.latest_id())
        self.assertEqual(lineage['dependencies'][self.table]['version_id'], output['dataset_versions'][self.table])
        self.assertEqual(lineage['result']['rows'], [[230]])
        files = {str(p): fingerprint(p) for p in self.w.root.rglob('*') if p.is_file() and p.suffix in {'.csv', '.xlsx', '.pdf'}}
        follow, _ = self.ask('What is the total again?', [
            lambda: call('export_result', query_id=self.latest_id(), name='unauthorized'), self.final])
        self.assertIn('not authorized', follow['trace'][0]['result']['error'])
        self.assertEqual(files, {str(p): fingerprint(p) for p in self.w.root.rglob('*') if p.is_file() and p.suffix in {'.csv', '.xlsx', '.pdf'}})

    def test_analysis_blocks_unrequested_export_and_quality_has_no_files(self):
        result, _ = self.ask('Analyze my Excel data, do not export files.', [
            call('check_quality', table_id=self.table, kind='duplicates', columns=['customer'], amount_column='revenue'),
            call('check_join', left_table=self.table, right_table=self.table, left_on=['customer'], right_on=['customer']),
            self.query(), lambda: call('export_result', query_id=self.latest_id()), self.final])
        self.assertEqual(result['trace'][0]['result']['exposed_amount'], '1149')
        self.assertIn('not authorized', result['trace'][3]['result']['error'])
        self.assert_no_artifacts()

    def test_explicit_formats_excel_pdf_and_lineage_actual_values(self):
        result, _ = self.ask('Export June revenue to CSV, Excel, and PDF.', [
            self.query(), lambda: call('export_result', query_id=self.latest_id(), name='all_formats'), self.final])
        self.assertEqual(result['status'], 'answered')
        files = result['trace'][1]['result']
        import openpyxl
        book = openpyxl.load_workbook(files['excel']['path'], data_only=True)
        self.assertEqual(book['Result']['A2'].value, 230)
        self.assertIn(files['query_id'], book['Provenance']['A2'].value)
        book.close()
        from pypdf import PdfReader
        text = '\n'.join(page.extract_text() for page in PdfReader(files['pdf']['path']).pages)
        self.assertIn('230', text)
        self.assertIn(files['query_id'], text)
        lineage = json.loads(Path(files['lineage']).read_text())
        for name, digest in lineage['export']['files_sha256'].items():
            self.assertEqual(fingerprint(Path(files['lineage']).parent / name), digest)

    def test_unknown_id_recovery_requires_actual_query_result(self):
        result, _ = self.ask('Export June revenue to CSV.', [
            call('export_result', query_id='made_up', name='unknown'), self.query(),
            lambda: call('export_result', query_id=self.latest_id(), name='recovered'), self.final])
        self.assertIn('Unknown query_id', result['trace'][0]['result']['error'])
        self.assertFalse((self.w.root / 'outputs' / 'unknown').exists())
        self.assertEqual(pd.read_csv(result['trace'][2]['result']['csv']).revenue.tolist(), [230])
        self.assertEqual(result['status'], 'answered')

    def test_failed_or_unperformed_export_is_incomplete(self):
        result, _ = self.ask('Export this result to CSV.', [
            call('export_result', query_id='made_up'), {'role': 'assistant', 'content': 'I will retry.'}])
        self.assertEqual(result['status'], 'incomplete')
        self.assertIn('incomplete', result['answer'])
        self.assert_no_artifacts()

    def test_dynamic_model_is_not_replaced_by_snapshot(self):
        result, _ = self.ask('Create an auditable Excel model.', [
            self.query(), lambda: call('export_result', query_id=self.latest_id(), name='not_a_model'), self.final])
        self.assertEqual(result['status'], 'incomplete')
        self.assertIn('No static substitute', result['trace'][1]['result']['error'])
        self.assert_no_artifacts()

    def test_explicit_programmatic_permission_and_format_scope(self):
        result, _ = self.ask('Please make the requested artifact.', [
            self.query(), lambda: call('export_result', query_id=self.latest_id(), name='wrong', formats=['pdf']),
            lambda: call('export_result', query_id=self.latest_id(), name='right', formats=['csv']), self.final], export_formats=['csv'])
        self.assertIn('exceed', result['trace'][1]['result']['error'])
        self.assertEqual(result['trace'][2]['result']['status'], 'exported')

    def test_intent_is_conservative(self):
        for question in ['Analyze the Excel model', 'Which Excel customers grew?', 'Do not export to CSV.',
                         "Don't generate a PDF", '不需要导出 CSV', '无需生成 Excel',
                         'How do I export to CSV?', 'Explain how to generate a PDF',
                         'Can I save an Excel file?', 'What does export to CSV mean?',
                         'Create summary statistics from the Excel dataset', 'Create a filter in Excel']:
            with self.subTest(question=question):
                self.assertEqual(export_intent(question)[0], [])
        self.assertEqual(export_intent('Export this result to CSV.')[0], ['csv'])
        self.assertEqual(export_intent('生成 PDF 摘要')[0], ['pdf'])
        self.assertTrue(export_intent('Create an auditable Excel model.')[1])

    def test_stale_handle_feedback_and_new_query_recovery(self):
        result, _ = self.ask('What was June revenue?', [self.query(), self.final])
        old = result['trace'][0]['result']['query_id']
        # Same logical dataset, fresh version and values, without changing the source file.
        frame = self.w.frames[self.table].copy()
        frame.loc[frame.customer == 'B', 'revenue'] = 90
        self.w.register(frame, self.table, {'kind': 'fixture'})
        result, _ = self.ask('Export this result to CSV.', [
            call('export_result', query_id=old, name='stale'), self.query(),
            lambda: call('export_result', query_id=self.latest_id(), name='new'),
            lambda: self.final('Updated June revenue is 240.')])
        self.assertIn('Stale', result['trace'][0]['result']['error'])
        self.assertIn('new query', result['trace'][0]['result']['error'])
        self.assertFalse((self.w.root / 'outputs' / 'stale').exists())
        self.assertEqual(pd.read_csv(result['trace'][2]['result']['csv']).revenue.tolist(), [240])
        self.assertEqual(result['status'], 'answered')

    def test_final_answer_cannot_reuse_stale_cited_evidence(self):
        self.ask('What was June revenue?', [self.query(), self.final])
        old = self.latest_id()
        self.w.register(self.w.frames[self.table], self.table, {'kind': 'fixture'})
        result, _ = self.ask('What is the total now?', [
            {'role': 'assistant', 'content': 'The total is 230. Evidence: ' + old}])
        self.assertEqual(result['status'], 'incomplete')
        self.assertIn('withheld', result['answer'])
        self.assertNotIn('230', result['answer'])


if __name__ == '__main__':
    unittest.main()
