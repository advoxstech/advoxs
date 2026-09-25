"""Exercise deployment credential persistence using disposable environment files."""

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap", ROOT / "scripts" / "ensure_agents_api_key.py"
)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class AgentsKeyBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / ".env"

    def test_generates_once_and_preserves_other_settings(self):
        unrelated = '# settings\nJWT_SECRET="keep-this"\nOTHER="two\nlines"\n'
        for entry in ("", "AGENTS_API_KEY=\n", 'AGENTS_API_KEY="   "\n'):
            with self.subTest(entry=entry):
                self.path.write_text(unrelated + entry)
                output = io.StringIO()
                with (
                    contextlib.redirect_stdout(output),
                    contextlib.redirect_stderr(output),
                ):
                    self.assertTrue(bootstrap.ensure_agents_api_key(self.path))
                values = dotenv_values(self.path)
                self.assertRegex(values["AGENTS_API_KEY"], r"^[0-9a-f]{64}$")
                self.assertEqual(values["JWT_SECRET"], "keep-this")
                self.assertEqual(values["OTHER"], "two\nlines")
                self.assertTrue(self.path.read_text().startswith(unrelated))
                self.assertEqual(output.getvalue(), "")
                before = self.path.read_bytes()
                self.assertFalse(bootstrap.ensure_agents_api_key(self.path))
                self.assertEqual(self.path.read_bytes(), before)
                if os.name == "posix":
                    self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_existing_key_is_not_rotated(self):
        original = b"AGENTS_API_KEY='existing-key'\nOTHER=yes\n"
        self.path.write_bytes(original)
        self.assertFalse(bootstrap.ensure_agents_api_key(self.path))
        self.assertEqual(self.path.read_bytes(), original)

    def test_missing_file_is_rejected(self):
        with self.assertRaises(RuntimeError):
            bootstrap.ensure_agents_api_key(self.path)
        self.assertFalse(self.path.exists())

    def test_failed_replace_keeps_original_and_removes_temporary(self):
        self.path.write_text("AGENTS_API_KEY=\nOTHER=keep\n")
        original = self.path.read_bytes()
        with patch.object(bootstrap.os, "replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                bootstrap.ensure_agents_api_key(self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_duplicate_empty_entries_receive_same_key(self):
        self.path.write_text("AGENTS_API_KEY=\nexport AGENTS_API_KEY=\n")
        bootstrap.ensure_agents_api_key(self.path)
        lines = self.path.read_text().splitlines()
        self.assertEqual(lines[0].split("=", 1)[1], lines[1].split("=", 1)[1])


if __name__ == "__main__":
    unittest.main()
