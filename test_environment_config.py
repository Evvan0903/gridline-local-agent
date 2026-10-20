"""Environment loading contracts, using isolated dummy credentials only."""
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_analyst import config


class EnvironmentConfigurationTests(unittest.TestCase):
    def load(self, root):
        with patch.object(config, "PROJECT_ROOT", root):
            config.load_project_environment()

    def test_project_root_file_loads_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text("OPENAI_API_KEY=dummy-file-value\n")
            with patch.dict(os.environ, {}, clear=True), patch("os.getcwd", return_value="/"):
                self.load(root)
                self.assertTrue(os.environ.get("OPENAI_API_KEY") == "dummy-file-value")

    def test_existing_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text("OPENAI_API_KEY=dummy-file-value\n")
            for value in ("dummy-environment-value", ""):
                with self.subTest(empty=not value), patch.dict(os.environ, {"OPENAI_API_KEY": value}, clear=True):
                    self.load(root)
                    self.assertTrue(os.environ["OPENAI_API_KEY"] == value)

    def test_missing_file_is_optional(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            self.load(Path(temporary))
            self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_loading_does_not_print_or_log_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text("OPENAI_API_KEY=dummy-file-value\n")
            output, errors = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {}, clear=True), redirect_stdout(output), redirect_stderr(errors), patch("dotenv.main.logger") as logger:
                self.load(root)
            self.assertFalse(output.getvalue())
            self.assertFalse(errors.getvalue())
            self.assertFalse(logger.mock_calls)

    def test_project_env_remains_gitignored(self):
        ignore = Path(__file__).resolve().parent / ".gitignore"
        self.assertIn(".env", ignore.read_text().splitlines())
