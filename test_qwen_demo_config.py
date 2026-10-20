"""Demo-runner tests only: mocked metadata, no inference and no engine rebuild."""
import ast
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from examples import demo_qwen


class DemoConfigurationTests(unittest.TestCase):
    def response(self, data):
        response = Mock()
        response.json.return_value = data
        return response

    def test_exact_tag_required_no_alias_substitution(self):
        with patch.object(demo_qwen.requests, "get", return_value=self.response(
                {"models": [{"name": "qwen3:27b"}, {"name": "qwen3:8b"}]})), \
                patch.object(demo_qwen.requests, "post") as post:
            result = demo_qwen.check_model("qwen3.8:27b", "http://localhost:11434")
        self.assertFalse(result["installed"])
        self.assertFalse(result["available"])
        post.assert_not_called()

    def test_installed_tag_requires_tool_capability_metadata_only(self):
        with patch.object(demo_qwen.requests, "get", return_value=self.response(
                {"models": [{"name": "chosen:tag", "digest": "abc"}]})), \
                patch.object(demo_qwen.requests, "post", return_value=self.response(
                    {"capabilities": ["completion", "tools"]})) as post:
            result = demo_qwen.check_model("chosen:tag", "http://localhost:11434")
        self.assertTrue(result["available"])
        post.assert_called_once_with("http://localhost:11434/api/show", json={"model": "chosen:tag"}, timeout=30)

    def test_non_tool_model_is_blocked(self):
        with patch.object(demo_qwen.requests, "get", return_value=self.response(
                {"models": [{"name": "chosen:tag"}]})), \
                patch.object(demo_qwen.requests, "post", return_value=self.response(
                    {"capabilities": ["completion"]})):
            result = demo_qwen.check_model("chosen:tag", "http://localhost:11434")
        self.assertTrue(result["installed"])
        self.assertFalse(result["available"])

    def test_unavailable_or_check_only_never_starts_workspace_or_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({"model": "next:tag", "ollama_url": "http://localhost:11434"}))
            for available, arguments in [(False, []), (True, ["--check-only"])]:
                with self.subTest(available=available), \
                        patch.object(demo_qwen, "ROOT", root), \
                        patch.object(demo_qwen, "CONFIG_PATH", config), \
                        patch.object(demo_qwen, "check_model", return_value={"available": available}) as check, \
                        patch.object(demo_qwen, "Workspace") as workspace, \
                        patch.object(demo_qwen, "AnalystAgent") as agent, redirect_stdout(io.StringIO()):
                    result = demo_qwen.main(["--model", "selected:tag", *arguments])
                    self.assertEqual(result, 0 if available else 2)
                    check.assert_called_once_with("selected:tag", "http://localhost:11434")
                    workspace.assert_not_called()
                    agent.assert_not_called()

    def test_questions_and_engine_fingerprints_match_archived_baseline(self):
        config = json.loads(demo_qwen.CONFIG_PATH.read_text())
        baseline = json.loads((demo_qwen.ROOT / config["baseline_manifest"]).read_text())
        tree = ast.parse(Path(demo_qwen.__file__).read_text())
        assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "cases" for target in node.targets))
        questions = [item.elts[0].value for item in assignment.value.elts]
        self.assertEqual(questions, baseline["questions"])
        self.assertEqual(hashlib.sha256(json.dumps(questions, ensure_ascii=False).encode()).hexdigest(), baseline["questions_sha256"])
        for name, digest in baseline["engine_sha256"].items():
            self.assertEqual(hashlib.sha256((demo_qwen.ROOT / name).read_bytes()).hexdigest(), digest, name)


if __name__ == "__main__":
    unittest.main()
