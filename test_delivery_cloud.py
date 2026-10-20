"""Offline SDK transport tests for the new explicitly consented delivery path."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import OpenAI

from local_analyst.delivery_cloud import (DeliveryCloud, LUNA, SOL, CHECKS, RULES,
    STAGES, code_fingerprints, digest, scope_id)
from local_analyst.config import PROJECT_ROOT
from local_analyst.providers import ProviderBlocked


class DeliveryCloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []
        self.failure = None
        self.status = 'completed'
        def send(req):
            self.calls.append(json.loads(req.content))
            if self.failure:
                raise self.failure
            return httpx.Response(200, json={
                'id': 'resp_offline', 'object': 'response', 'created_at': 1,
                'model': self.calls[-1]['model'], 'status': self.status,
                'output': [{'type': 'message', 'id': 'msg_offline', 'role': 'assistant',
                    'status': 'completed', 'content': [{'type': 'output_text', 'text':
                    'Source-grounded offline fixture, not a real model answer.', 'annotations': []}]}],
                'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}})
        self.client = OpenAI(api_key='offline-dummy', max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(send)))
        self.addCleanup(self.client.close)
        self.sources = {p: digest(PROJECT_ROOT/p) for p in (
            'data/Sales Compensation Analyst Case Study.xlsx', 'Clipboard_Health_Case_Study_Rules.md')}
        self.consent = {'paid_authorized': True, 'data_authorized': True,
            'human_confirmation': 'OFFLINE TEST FIXTURE ONLY', 'confidential_mode': False,
            'run_id': 'one-staged-run', 'models': [LUNA, SOL], 'sources': self.sources,
            'aggregate_cap_usd': '2.00', 'sol_cap_usd': '1.00'}
        self.cp, self.rp = self.root/'consent.json', self.root/'readiness.json'
        self.evidence = self.root/'mock_offline_evidence.txt'
        self.evidence.write_text('mock readiness evidence; not actual engineering acceptance')
        self.readiness = {'status': 'PASS', 'checks': dict.fromkeys(CHECKS, 'PASS'),
            'engine_sha256': code_fingerprints(),
            'evidence_sha256': {str(self.evidence): digest(self.evidence)}}
        self.persist()

    def persist(self):
        self.cp.write_text(json.dumps(self.consent))
        self.rp.write_text(json.dumps(self.readiness))

    def make(self):
        c = DeliveryCloud(self.root/'run', self.cp, self.rp,
            clients={LUNA: self.client, SOL: self.client})
        self.addCleanup(c.close)
        return c

    def reviewed(self, c):
        c.request('rules', 'Read the complete source rules supplied in a real run.')
        self.review(c)

    def review(self, c, **changes):
        state = c._state()
        stage = 'rules_correction' if 'rules_correction' in state['stages'] else 'rules'
        r = {'status': 'VERIFIED', 'source_scope': scope_id(self.sources),
             'reviewed_stage': stage,
             'response_sha256': hashlib.sha256(state['stages'][stage]['response']['content'].encode()).hexdigest(),
             'source_anchored_checks': [{'id': x, 'status': 'VERIFIED',
                 'source_references': ['07, independently checked fixture']} for x in RULES],
             'unresolved_explicit_rule_conflicts': []}
        r.update(changes)
        p = self.root/'review.json'
        p.write_text(json.dumps(r))
        c.review_rules(p)

    def escalation(self):
        return {'trigger': 'exception_interaction', 'unresolved_question': 'Which bounded attribution is supported?',
            'source_passages': ['7.8 source excerpt fixture'],
            'candidate_interpretations': ['named method A', 'named method B'],
            'missing_evidence_only': False}

    def test_pending_consent_before_approval_or_sdk(self):
        self.consent['paid_authorized'] = False
        self.persist()
        with self.assertRaisesRegex(ProviderBlocked, 'NEW_EXPLICIT'):
            self.make()
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root/'run/cloud_spend.sqlite3').exists())

    def test_model_scope_and_confidential_mode(self):
        for change, error in [({'models': [LUNA, 'gpt-6-astra']}, 'MODEL_SCOPE'),
                              ({'confidential_mode': True}, 'NEW_EXPLICIT')]:
            old = dict(self.consent)
            self.consent.update(change)
            self.persist()
            with self.assertRaisesRegex(ProviderBlocked, error):
                self.make()
            self.consent = old
        self.assertFalse(self.calls)

    def test_stale_readiness_blocks(self):
        self.readiness['engine_sha256'] = {}
        self.persist()
        with self.assertRaisesRegex(ProviderBlocked, 'CURRENT_OFFLINE'):
            self.make()
        self.assertFalse(self.calls)

    def test_missing_readiness_check_blocks(self):
        self.readiness['checks']['dynamic_excel'] = 'NOT_RUN'
        self.persist()
        with self.assertRaisesRegex(ProviderBlocked, 'CURRENT_OFFLINE'):
            self.make()

    def test_changed_offline_evidence_blocks(self):
        self.evidence.write_text('changed')
        with self.assertRaisesRegex(ProviderBlocked, 'EVIDENCE_CHANGED'):
            self.make()

    def test_source_hash_scope_blocks(self):
        self.consent['sources']['Clipboard_Health_Case_Study_Rules.md'] = 'wrong'
        self.persist()
        with self.assertRaisesRegex(ProviderBlocked, 'SOURCE_SCOPE_CHANGED'):
            self.make()

    def test_revocation_checked_on_every_dispatch(self):
        c = self.make()
        self.consent['data_authorized'] = False
        self.persist()
        with self.assertRaisesRegex(ProviderBlocked, 'CONSENT_OR_READINESS_CHANGED'):
            c.request('rules', 'source')
        self.assertFalse(self.calls)

    def test_rules_first_and_source_based_review_gate(self):
        c = self.make()
        with self.assertRaisesRegex(ProviderBlocked, 'RULES_FIRST'):
            c.request('exceptions', 'case evidence')
        c.request('rules', 'source rules')
        with self.assertRaisesRegex(ProviderBlocked, 'VERIFIED_RULES_GATE'):
            c.request('exceptions', 'case evidence')
        self.review(c)
        c.request('exceptions', 'source-bound case evidence, not oracle totals')
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(not x['tools'] and not x['store'] for x in self.calls))

    def test_review_bound_to_actual_response_and_all_rules(self):
        c = self.make()
        c.request('rules', 'source')
        for changes in ({'response_sha256': 'unrelated'}, {'source_anchored_checks': []},
                        {'unresolved_explicit_rule_conflicts': ['halfcredit']}):
            with self.assertRaisesRegex(ProviderBlocked, 'SOURCE_BASED_RULE_REVIEW'):
                self.review(c, **changes)

    def test_rule_correction_invalidates_previous_review(self):
        c = self.make()
        self.reviewed(c)
        c.request('rules_correction', 'Explicit source provision, correct prior conflict.')
        with self.assertRaisesRegex(ProviderBlocked, 'VERIFIED_RULES_GATE'):
            c.request('exceptions', 'evidence')
        self.review(c)
        c.request('exceptions', 'evidence')

    def test_sol_conditional_no_fallback_or_substitution(self):
        c = self.make()
        c.request('rules', 'source')
        with self.assertRaisesRegex(ProviderBlocked, 'BOUNDED_MATERIAL'):
            c.request('sol_escalation', 'do the whole case')
        esc = self.escalation()
        esc['missing_evidence_only'] = True
        with self.assertRaisesRegex(ProviderBlocked, 'BOUNDED_MATERIAL'):
            c.request('sol_escalation', 'invent missing payments', escalation=esc)
        c.request('sol_escalation', 'minimal bounded source excerpts', escalation=self.escalation())
        self.assertEqual([x['model'] for x in self.calls], [LUNA, SOL])
        with self.assertRaisesRegex(ProviderBlocked, 'NO_RETRY'):
            c.request('sol_escalation', 'repeat', escalation=self.escalation())

    def test_sol_separate_data_consent(self):
        self.consent['models'] = [LUNA]
        self.persist()
        c = self.make()
        c.request('rules', 'source')
        with self.assertRaisesRegex(ProviderBlocked, 'MODEL_DATA_CONSENT'):
            c.request('sol_escalation', 'bounded', escalation=self.escalation())

    def test_aggregate_eight_requests_shared_across_models(self):
        c = self.make()
        self.reviewed(c)
        for stage in STAGES:
            if stage == 'rules':
                continue
            if stage == 'rules_correction':
                c.request(stage, 'source-grounded correction')
                self.review(c)
            else:
                c.request(stage, 'bounded evidence', escalation=self.escalation() if stage == 'sol_escalation' else None)
        audit = c.audit()
        self.assertEqual(sum(x['requests'] for x in audit['models'].values()), 8)
        self.assertEqual(audit['models'][LUNA]['requests'], 7)
        self.assertEqual(audit['models'][SOL]['requests'], 1)
        with self.assertRaisesRegex(ProviderBlocked, 'REQUEST_LIMIT'):
            c.ledger.reserve(c._state()['approvals'][LUNA], c.consent['run_id'], c.scope,
                c.consent['run_id'], LUNA, 0)
        self.assertEqual(len(self.calls), 8)

    def test_budget_denial_before_sdk(self):
        self.consent.update(aggregate_cap_usd='0.001', sol_cap_usd='0.001')
        self.persist()
        c = self.make()
        with self.assertRaisesRegex(ProviderBlocked, 'BUDGET_EXCEEDED'):
            c.request('rules', 'source')
        self.assertFalse(self.calls)

    def test_failed_request_no_retry_or_cloud_fallback(self):
        self.failure = httpx.ConnectError('offline simulated transport failure')
        c = self.make()
        with self.assertRaisesRegex(ProviderBlocked, 'OPENAI_REQUEST_FAILED'):
            c.request('rules', 'source')
        with self.assertRaisesRegex(ProviderBlocked, 'NO_RETRY'):
            c.request('rules', 'source')
        with self.assertRaisesRegex(ProviderBlocked, 'UNCERTAIN_STAGE'):
            c.request('sol_escalation', 'fallback', escalation=self.escalation())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(c.audit()['models'][LUNA]['unknown_usage_requests'], 1)
        c2 = self.make()
        with self.assertRaisesRegex(ProviderBlocked, 'NO_RETRY'):
            c2.request('rules', 'restart does not repeat')
        self.assertEqual(len(self.calls), 1)

    def test_incomplete_with_usage_keeps_charge_and_stops(self):
        self.status = 'incomplete'
        c = self.make()
        with self.assertRaisesRegex(ProviderBlocked, 'INCOMPLETE'):
            c.request('rules', 'source')
        self.assertEqual(c.audit()['models'][LUNA]['unknown_usage_requests'], 0)
        self.assertEqual(c.audit()['models'][LUNA]['input_tokens'], 100)
        self.assertEqual(len(self.calls), 1)

    def test_bounded_context_and_persistent_lease(self):
        c = self.make()
        with self.assertRaisesRegex(ProviderBlocked, 'CONTEXT_LIMIT'):
            c.request('rules', 'x'*100000)
        (c.root/'dispatch.lock').write_text('interrupted dispatch; do not guess')
        with self.assertRaisesRegex(ProviderBlocked, 'BUSY_OR_INTERRUPTED'):
            c.request('rules', 'source')
        self.assertFalse(self.calls)


if __name__ == '__main__':
    unittest.main()
