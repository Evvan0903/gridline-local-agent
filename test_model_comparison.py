"""Configuration and comparison-observer regressions; no live inference."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

from examples.compare_models import diagnostics, run_question, main as compare_main
from examples.demo_qwen import evaluate_result
from local_analyst import Workspace
from local_analyst.agent import AnalystAgent
from local_analyst.config import resolve_model


class ModelComparisonTests(unittest.TestCase):
    def test_same_model_cannot_silently_be_compared_to_itself(self):
        with tempfile.TemporaryDirectory() as tmp, patch("examples.compare_models.check_model") as check:
            with self.assertRaisesRegex(ValueError, "two distinct exact tags"):
                compare_main(["--output", tmp, "--baseline-model", "same:tag", "--test-model", "same:tag"])
            check.assert_not_called()

    def test_agent_default_and_explicit_override_use_shared_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace(Path(tmp) / "workspace", restore=False)
            with patch.dict(os.environ, {}, clear=True), patch("local_analyst.config.settings", return_value={"model": "configured:tag"}):
                self.assertEqual(AnalystAgent(workspace).model, "configured:tag")
                self.assertEqual(AnalystAgent(workspace, model="override:tag").model, "override:tag")
            workspace.db.close()

    def test_configuration_precedence_and_purpose(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"model": "default:tag", "test_model": "test:tag"}))
            with patch.dict(os.environ, {"LOCAL_ANALYST_CONFIG": str(path)}, clear=True):
                self.assertEqual(resolve_model(), "default:tag")
                self.assertEqual(resolve_model(purpose="test_model"), "test:tag")
                with patch.dict(os.environ, {"OLLAMA_MODEL": "env:tag"}):
                    self.assertEqual(resolve_model(), "env:tag")
                    self.assertEqual(resolve_model("cli:tag"), "cli:tag")
                    with self.assertRaises(ValueError):
                        resolve_model(" ")

    def test_original_evaluator_is_not_strengthened_silently(self):
        result = {"answer": "107052", "trace": [{"tool": "query_sql", "result": {"query_id": "q"}}]}
        self.assertTrue(evaluate_result(result, [107052])["passed"])
        self.assertFalse(evaluate_result(result, [123])["passed"])
        self.assertFalse(evaluate_result({"answer": "107052"}, [107052])["passed"])

    def test_failure_preserves_request_error_and_partial_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace(Path(tmp) / "workspace", restore=False)
            with patch("requests.post", side_effect=requests.Timeout("scripted failure")):
                result = run_question(workspace, "chosen:tag", "http://localhost:11434", "count rows", [2], [[2]], Path(tmp) / "q1")
            self.assertFalse(result["evaluation"]["passed"])
            self.assertIn("scripted failure", result["exception"])
            events = [json.loads(line) for line in (Path(tmp) / "q1/http.jsonl").read_text().splitlines()]
            self.assertEqual([e["kind"] for e in events], ["request", "exception"])
            self.assertEqual(events[0]["payload"]["model"], "chosen:tag")
            history = json.loads((Path(tmp) / "q1/history.json").read_text())
            self.assertEqual(next(m["content"] for m in reversed(history) if m["role"] == "user"), "count rows")
            self.assertEqual(result["status"], "incomplete")
            self.assertIn("no completed analysis claimed", history[-1]["content"])
            self.assertTrue((Path(tmp) / "q1/result.json").exists())
            workspace.db.close()

    def test_observer_preserves_payload_and_real_tool_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace(Path(tmp) / "workspace", restore=False)
            workspace.register(pd.DataFrame({"value": [1, 2]}), "t_example", {"label": "example"})
            sent = []
            def respond(url, **kwargs):
                sent.append(json.loads(json.dumps(kwargs["json"])))
                response = Mock(status_code=200)
                message = ({"role": "assistant", "content": "", "tool_calls": [{"function": {
                    "name": "query_sql", "arguments": {"sql": "SELECT COUNT(*) FROM t_example"}}}]}
                    if len(sent) == 1 else {"role": "assistant", "content": "There are 2 rows."})
                response.json.return_value = {"message": message}
                return response
            with patch("requests.post", side_effect=respond):
                result = run_question(workspace, "chosen:tag", "http://localhost:11434", "count rows", [2], [[2]], Path(tmp) / "q1")
            self.assertTrue(result["evaluation"]["passed"])
            self.assertTrue(result["diagnostics"]["source_result_match"])
            self.assertEqual(sent[0]["options"], {"temperature": 0, "num_ctx": 16384})
            self.assertFalse(sent[0]["think"])
            self.assertEqual(sent[0]["messages"][-1], {"role": "user", "content": "count rows"})
            self.assertEqual(sent[1]["messages"][-1]["role"], "tool")
            workspace.db.close()

    def test_table_id_and_result_diagnostics_are_separate_from_score(self):
        result = {"answer": "2", "trace": [{"tool": "query_sql", "arguments": {
            "sql": "SELECT COUNT(*) FROM invented_table"}, "result": {"query_id": "q", "rows": [[99]]}}]}
        diagnostic = diagnostics(result, [[2]], {"t_real": {}})
        self.assertFalse(diagnostic["source_result_match"])
        self.assertEqual(diagnostic["invalid_sql_table_ids"], ["invented_table"])
        self.assertTrue(evaluate_result(result, [2])["passed"])


if __name__ == "__main__":
    unittest.main()
