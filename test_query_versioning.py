"""Regression: saved evidence must never describe a different dataset version."""
from decimal import Decimal
import copy
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from local_analyst.workspace import Workspace
from local_analyst.workspace import fingerprint
from local_analyst.modeling import run_plan
from local_analyst.exporting import export_query
from test_local_analyst import simple_plan


class QueryVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.w = Workspace(self.root / 'workspace')
        self.addCleanup(self.w.db.close)
        source = Path('examples/collections/invoices.csv')
        self.source = self.root / 'invoices.csv'
        self.source.write_bytes(source.read_bytes())
        self.table = self.w.load(self.source, 'collections')['tables'][0]['table_id']
        self.plan = simple_plan(self.table)

    def calculate(self, rate):
        return run_plan(self.w, self.plan, {'rate': rate})

    def total(self):
        return self.w.query('SELECT SUM(local_fee) AS total FROM model_baseline_converted')

    def test_recomputed_model_rejects_old_export(self):
        self.calculate('0.1')
        old = self.total()
        self.assertEqual(Decimal(str(old['rows'][0][0])), Decimal(35))
        self.calculate('0.2')
        fresh = self.total()
        self.assertEqual(Decimal(str(fresh['rows'][0][0])), Decimal(70))
        with self.assertRaisesRegex(ValueError, '(?i)stale.*new query'):
            export_query(self.w, old['query_id'], 'must_not_exist', excel=False)
        self.assertFalse((self.w.root / 'outputs' / 'must_not_exist').exists())

    def test_new_query_export_full_values_and_lineage(self):
        self.calculate('0.1')
        old = self.total()
        old_evidence = copy.deepcopy(self.w.queries[old['query_id']])
        self.calculate('0.2')
        with self.assertRaisesRegex(ValueError, '(?i)stale.*new query'):
            self.w.query_evidence(old['query_id'])
        fresh = self.total()
        self.assertNotEqual(old['dataset_versions'], fresh['dataset_versions'])
        files = export_query(self.w, fresh['query_id'], 'fresh', excel=False)
        self.assertEqual(pd.read_csv(files['csv']).total.tolist(), [70])
        lineage = json.loads(Path(files['lineage']).read_text())
        self.assertEqual(Decimal(lineage['result']['rows'][0][0]), Decimal(70))
        self.assertEqual(lineage['result_digest'], fresh['result_digest'])
        self.assertEqual(lineage['export']['row_count'], 1)
        self.assertEqual(lineage['export']['files_sha256']['result.csv'], fingerprint(files['csv']))
        dependencies = lineage['dependencies']
        self.assertEqual(set(dependencies), {self.table, 'model_baseline_eligible', 'model_baseline_fees', 'model_baseline_converted'})
        for table, meta in dependencies.items():
            self.assertEqual(meta['version_id'], files['dataset_versions'][table])
            for parent, version in meta.get('parents', {}).items():
                self.assertEqual(version, dependencies[parent]['version_id'])
        self.assertEqual(dependencies[self.table]['source_sha256'], fingerprint(self.source))
        self.assertEqual(dependencies['model_baseline_fees']['parameters']['rate'], '0.2')
        self.assertEqual(dependencies['model_baseline_fees']['assumptions'], self.plan['assumptions'])
        self.assertEqual(self.w.queries[old['query_id']], old_evidence)

    def test_transitive_parent_change_blocks_new_query_and_export(self):
        self.calculate('0.1')
        saved = self.total()
        # Replace ONLY an upstream table, leaving the old final table in memory.
        original = self.w.frames['model_baseline_eligible']
        self.w.register(original, 'model_baseline_eligible', {'kind': 'derived'})
        for action in [self.total, lambda: self.w.query_evidence(saved['query_id']),
                       lambda: export_query(self.w, saved['query_id'], 'stale', excel=False)]:
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, '(?i)stale.*new query'):
                action()

    def test_identical_recalculation_still_gets_new_derived_generation(self):
        self.calculate('0.1')
        saved = self.total()
        self.calculate('0.1')
        self.assertNotEqual(saved['dataset_versions'], self.total()['dataset_versions'])
        with self.assertRaisesRegex(ValueError, '(?i)stale'):
            self.w.query_evidence(saved['query_id'])

    def test_source_file_change_requires_reload_and_new_query(self):
        self.calculate('0.1')
        saved = self.total()
        self.source.write_text(self.source.read_text().replace('150', '160'))
        with self.assertRaisesRegex(ValueError, '(?i)stale source.*new query'):
            self.w.query_evidence(saved['query_id'])

    def test_raw_versions_restore_and_changed_raw_invalidates(self):
        saved = self.w.query(f'SELECT SUM(gross) FROM "{self.table}"')
        first = self.w.catalog[self.table]['version_id']
        restored = Workspace(self.w.root)
        self.addCleanup(restored.db.close)
        self.assertEqual(restored.catalog[self.table]['version_id'], first)
        self.assertEqual(restored.query_evidence(saved['query_id'])['status'], 'validated_current')
        self.source.write_text(self.source.read_text().replace('150', '160'))
        restored.load(self.source, 'collections')
        self.assertNotEqual(restored.catalog[self.table]['version_id'], first)
        with self.assertRaisesRegex(ValueError, '(?i)stale.*new query'):
            restored.query_evidence(saved['query_id'])

    def test_derived_not_restored_is_explicitly_stale(self):
        self.calculate('0.1')
        saved = self.total()
        restored = Workspace(self.w.root)
        self.addCleanup(restored.db.close)
        with self.assertRaisesRegex(ValueError, '(?i)stale.*new query'):
            restored.query_evidence(saved['query_id'])

    def test_unknown_and_legacy_handles_have_actionable_errors(self):
        with self.assertRaisesRegex(ValueError, 'Unknown query_id.*new query'):
            export_query(self.w, 'invented_id', 'nope', excel=False)
        self.w.queries['legacy'] = {'sql': 'SELECT 35', 'parameters': {}, 'sources': {}}
        with self.assertRaisesRegex(ValueError, '(?i)unversioned.*new query'):
            self.w.query_evidence('legacy')

    def test_full_export_never_uses_truncated_rows_or_strings(self):
        self.w.register(pd.DataFrame({'n': range(100005), 'text': ['x' * 350] * 100005}), 'large', {'kind': 'fixture'})
        saved = self.w.query('SELECT * FROM large ORDER BY n', max_rows=2)
        self.assertEqual(len(saved['rows']), 2)
        self.assertTrue(saved['truncated'])
        self.assertEqual(len(saved['rows'][0][1]), 300)
        self.assertEqual(saved['result_digest']['row_count'], 100005)
        files = export_query(self.w, saved['query_id'], 'large', excel=False)
        frame = pd.read_csv(files['csv'])
        self.assertEqual(len(frame), 100005)
        self.assertEqual(frame.n.sum(), 5000450010)
        self.assertTrue(frame.text.str.len().eq(350).all())

    def test_in_place_mutation_and_result_mismatch_fail_closed(self):
        self.calculate('0.1')
        saved = self.total()
        self.w.frames['model_baseline_converted'].loc[0, 'local_fee'] = Decimal(999)
        with self.assertRaisesRegex(ValueError, '(?i)stale in-memory'):
            export_query(self.w, saved['query_id'], 'nope', excel=False)
        self.calculate('0.1')
        saved = self.total()
        self.w.queries[saved['query_id']]['result_digest']['sha256'] = 'corrupt'
        with self.assertRaisesRegex(ValueError, '(?i)non-reproducible.*new query'):
            export_query(self.w, saved['query_id'], 'nope', excel=False)

    def test_original_csv_and_xlsx_unchanged(self):
        workbook = Path('data/Sales Compensation Analyst Case Study.xlsx')
        before = {p: fingerprint(p) for p in [self.source, workbook]}
        self.calculate('0.1')
        saved = self.total()
        export_query(self.w, saved['query_id'], 'immutable', excel=False)
        imported = self.w.load(workbook, 'acceptance')
        self.assertGreaterEqual(len(imported['tables']), 11)
        for path, digest in before.items():
            self.assertEqual(fingerprint(path), digest)

    def test_dynamic_model_snapshot_substitution_and_overwrite_rejected(self):
        saved = self.w.query('SELECT 35 AS amount')
        with self.assertRaisesRegex(ValueError, 'dynamic Excel models'):
            export_query(self.w, saved['query_id'], 'model', kind='dynamic_model')
        self.assertFalse((self.w.root / 'outputs').exists())
        files = export_query(self.w, saved['query_id'], 'preserved', excel=False)
        digest = fingerprint(files['csv'])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            export_query(self.w, saved['query_id'], 'preserved', excel=False)
        self.assertEqual(fingerprint(files['csv']), digest)


if __name__ == '__main__':
    unittest.main()
