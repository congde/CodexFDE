from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workbench.ci_evidence import build_envelope


class CIEvidenceTests(unittest.TestCase):
    def test_envelope_binds_report_hash_and_run_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "harness-blocking.json"
            report.write_text('{"suite":"blocking","summary":{"decision":"pass"}}', encoding="utf-8")
            envelope = build_envelope(
                report, env={"GITHUB_SHA": "deadbeef", "GITHUB_RUN_ID": "9", "GITHUB_WORKFLOW": "gate"},
            )
            self.assertEqual(envelope["commit_sha"], "deadbeef")
            self.assertEqual(envelope["report_decision"], "pass")
            self.assertEqual(64, len(envelope["report_sha256"]))

    def test_missing_report_does_not_write_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "ci-evidence.json"
            env = os.environ.copy()
            env.update({"GITHUB_SHA": "a", "GITHUB_RUN_ID": "1"})
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "workbench.ci_evidence",
                 "--report", str(Path(temporary) / "missing.json"), "--output", str(output)],
                text=True, capture_output=True, env=env,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertFalse(output.exists())

    def test_missing_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "harness-blocking.json"
            report.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                build_envelope(report, env={})


if __name__ == "__main__":
    unittest.main()
