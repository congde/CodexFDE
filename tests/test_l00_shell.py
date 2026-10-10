"""Exercise the real L00 shell and protect the L01 handoff from overwrites."""
from __future__ import annotations

import ast
import json
import hashlib
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "docs/courses/L00/tools/prepare_shell.py"
PREPARE = runpy.run_path(str(TOOL))["prepare_shell"]
METADATA = "lesson-01-submission/00-isolation.json"
AIDS = ("docs/courses/L01/tools/evidence.py", "docs/courses/L01/tools/import_evidence.py")


class L00ShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="l00-shell-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.target = self.base / "personal-workbench"

    def snapshot(self, path):
        return {str(item.relative_to(path)): item.read_bytes() for item in path.rglob("*") if item.is_file()}

    def test_shell_paths_and_server_entry_points_match_reference(self):
        starter = ROOT / "docs/courses/L00/starter"
        for relative in ("workbench/__init__.py", "workbench/cli.py", "workbench/runtime_paths.py",
                         "workbench/workbench_server.py", "workbench_web/index.html",
                         "workbench_web/app.js", "workbench_web/styles.css", "pyproject.toml", "main.py",
                         "AGENTS.md", "docs/README.md", "docs/reference/工作台具体设计.md",
                         "agent/__init__.py", "eval/__init__.py", "harness_web/__init__.py"):
            self.assertTrue((starter / relative).is_file(), relative)
            self.assertTrue((ROOT / relative).is_file(), relative)
        interfaces = []
        for base in (starter, ROOT):
            tree = ast.parse((base / "workbench/workbench_server.py").read_text(encoding="utf-8-sig"))
            serve = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "serve")
            interfaces.append([arg.arg for arg in (*serve.args.args, *serve.args.kwonlyargs)])
            self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == "make_handler" for node in tree.body))
            app = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WorkbenchApp")
            self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == "health" for node in app.body))
        self.assertEqual(interfaces[0], interfaces[1])

    def test_create_shell_records_sources_without_future_implementations(self):
        result = PREPARE(self.target)
        self.assertEqual(result["source"], "personal_shell")
        metadata = json.loads((self.target / METADATA).read_text(encoding="utf-8"))
        self.assertEqual(metadata["source_root"], str(ROOT))
        self.assertEqual(metadata["path"], str(self.target))
        self.assertEqual(metadata["student_start"], {"lesson": 1, "source": "personal_shell"})
        self.assertEqual(metadata["shell_origin"], "course_template")
        self.assertEqual(metadata["template_role"], "copied_scaffold")
        self.assertEqual(metadata["student_acceptance"], "not_recorded")
        self.assertEqual(set(metadata["template_files_sha256"]), set(PREPARE.__globals__["SHELL_FILES"]))
        self.assertEqual(metadata["layout"], "aligned_directory_skeleton")
        self.assertEqual(metadata["missing_layout_files"], [])
        for directory in ("workbench", "workbench_web", "tests", "agent", "eval", "docs", "scripts", "deploy", "harness_web"):
            self.assertTrue((self.target / directory).is_dir(), directory)
        for relative in AIDS:
            self.assertEqual((self.target / relative).read_bytes(), (ROOT / relative).read_bytes())
        for relative in (".git", ".runtime", "workbench/bootstrap.py",
                         "docs/courses/L01/WORKBENCH_SPEC.md", "tests/test_l01_workbench_bootstrap.py",
                         "workbench/spec.py", "workbench/task_store.py", "workbench/execution.py",
                         "workbench/workflow.py", "eval/harness.py", "agent/loop.py", "agent/graph.py"):
            self.assertFalse((self.target / relative).exists(), relative)

    def test_cli_creates_empty_target_and_missing_five_commands_stay_missing(self):
        self.target.mkdir()
        prepared = subprocess.run([sys.executable, "-X", "utf8", str(TOOL), "--target", str(self.target)],
                                  capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertEqual(json.loads(prepared.stdout)["path"], str(self.target))
        status = subprocess.run([sys.executable, "-X", "utf8", "-m", "workbench.cli", "workbench-status"],
                                cwd=self.target, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(status.returncode, 2)
        self.assertIn("invalid choice", status.stderr)

    def test_root_entry_preserves_cli_help_and_defaults_to_same_server(self):
        PREPARE(self.target)
        default_entry = subprocess.run(
            [sys.executable, "-X", "utf8", "-c",
             "import main\n"
             "from unittest.mock import patch\n"
             "with patch('workbench.cli.main', return_value=17) as cli:\n"
             "    result = main.main([])\n"
             "    cli.assert_called_once_with(['serve-workbench'])\n"
             "    assert result == 17, result\n"],
            cwd=self.target, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(default_entry.returncode, 0, default_entry.stdout + default_entry.stderr)
        for command in (["main.py", "--help"], ["-m", "workbench.cli", "--help"]):
            result = subprocess.run([sys.executable, "-X", "utf8", *command], cwd=self.target,
                                    capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("serve-workbench", result.stdout)
        url, _, _ = self._start_shell(entry=("main.py", "serve-workbench"))
        with build_opener(ProxyHandler({})).open(url + "/api/health", timeout=3) as response:
            health = json.load(response)
        self.assertEqual(health["surface"], "workbench")
        self.assertEqual(health["runtime"], str(self.target / ".runtime/workbench"))
        self.assertFalse((self.target / ".runtime").exists())

    def test_git_ignores_l00_through_l04_records_but_keeps_source_files(self):
        PREPARE(self.target)
        git = ["git", "-c", "core.excludesFile="]
        initialized = subprocess.run([*git, "init", "--quiet"], cwd=self.target,
                                     capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        records = [f"lesson-{lesson:02d}-submission/record.md" for lesson in range(5)]
        for relative in records:
            path = self.target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Local learner record.\n", encoding="utf-8")
        ignored = subprocess.run([*git, "check-ignore", "-z", "--stdin"], cwd=self.target,
                                 input=("\0".join(records) + "\0").encode("utf-8"),
                                 capture_output=True, check=False)
        self.assertEqual(ignored.returncode, 0, ignored.stderr)
        self.assertEqual(set(ignored.stdout.decode("utf-8").split("\0")[:-1]), set(records))
        sources = ("main.py", "workbench/cli.py", "workbench_web/app.js",
                   "tests/test_workbench_server.py", "pyproject.toml",
                   "docs/reference/工作台具体设计.md")
        visible = subprocess.run([*git, "check-ignore", "-z", "--stdin"], cwd=self.target,
                                 input=("\0".join(sources) + "\0").encode("utf-8"),
                                 capture_output=True, check=False)
        self.assertEqual(visible.returncode, 1, visible.stderr)
        self.assertEqual(visible.stdout, b"")

    def test_starter_includes_independent_http_checks_that_really_run(self):
        PREPARE(self.target)
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "unittest", "tests.test_workbench_server", "-v"],
                                cwd=self.target, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ran 4 tests", result.stderr)
        self.assertFalse((self.target / ".runtime").exists())

    def test_legacy_ten_file_project_reports_missing_layout_without_writing_design(self):
        starter = ROOT / "docs/courses/L00/starter"
        for name in PREPARE.__globals__["CORE_SHELL_FILES"]:
            destination = self.target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((starter / name).read_bytes())
        before = self.snapshot(self.target)
        result = PREPARE(self.target, continue_l01=True)
        metadata = json.loads((self.target / METADATA).read_text(encoding="utf-8"))
        self.assertEqual(result["layout"], "legacy_minimal")
        self.assertEqual(metadata["layout"], "legacy_minimal")
        self.assertIn("docs/reference/工作台具体设计.md", result["missing_layout_files"])
        self.assertIn("main.py", result["missing_layout_files"])
        self.assertEqual(set(metadata["shell_files_sha256_at_preparation"]), set(PREPARE.__globals__["CORE_SHELL_FILES"]))
        for name, content in before.items():
            self.assertEqual((self.target / name).read_bytes(), content, name)
        for name in result["missing_layout_files"]:
            self.assertFalse((self.target / name).exists(), name)
        self.assertEqual(set(result["created_files"]), {*AIDS, METADATA})

    def test_refuses_nonempty_target_without_mutation(self):
        self.target.mkdir()
        (self.target / "my-notes.md").write_text("本人原始记录", encoding="utf-8")
        before = self.snapshot(self.target)
        with self.assertRaisesRegex(ValueError, "空目录"):
            PREPARE(self.target)
        self.assertEqual(before, self.snapshot(self.target))

    def test_refuses_reference_root_and_descendant(self):
        for path in (ROOT, ROOT / ".runtime" / "L00-tool-must-not-create"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "参考仓库"):
                PREPARE(path)
        with self.assertRaisesRegex(ValueError, "绝对路径"):
            PREPARE(Path("relative-shell"))

    def test_refuses_symlink_target_and_symlink_ancestor(self):
        real = self.base / "real"
        real.mkdir()
        link = self.base / "linked"
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"当前环境不能建立测试符号链接：{error}")
        for path in (link, link / "child"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "符号链接|重解析"):
                PREPARE(path)
        self.assertEqual(list(real.iterdir()), [])

    def test_continue_preserves_modified_shell_and_student_records(self):
        PREPARE(self.target)
        for relative in (*AIDS, METADATA):
            (self.target / relative).unlink()
        README = self.target / "README.md"
        README.write_text(README.read_text(encoding="utf-8") + "\n本人的壳设计解释。\n", encoding="utf-8")
        source = self.target / "workbench/cli.py"
        source.write_text(source.read_text(encoding="utf-8") + "\n# 本人修改记录\n", encoding="utf-8")
        record = self.target / "lesson-01-submission/00-handoff.md"
        record.write_text("首次判断尚未完成，不代填结果。", encoding="utf-8")
        before = self.snapshot(self.target)
        result = PREPARE(self.target, continue_l01=True)
        for relative, content in before.items():
            self.assertEqual((self.target / relative).read_bytes(), content, relative)
        self.assertEqual(set(result["created_files"]), {*AIDS, METADATA})
        after = self.snapshot(self.target)
        repeated = PREPARE(self.target, continue_l01=True)
        self.assertEqual(repeated["created_files"], [])
        self.assertEqual(after, self.snapshot(self.target))

    def test_first_continue_records_existing_source_not_template_authorship(self):
        starter = ROOT / "docs/courses/L00/starter"
        for name in PREPARE.__globals__["SHELL_FILES"]:
            destination = self.target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((starter / name).read_bytes())
        source = self.target / "workbench/cli.py"
        source.write_text(source.read_text(encoding="utf-8") + "\n# 个人项目当前版本，与对照版不同。\n", encoding="utf-8")
        before = self.snapshot(self.target)
        self.assertFalse((self.target / METADATA).exists())
        PREPARE(self.target, continue_l01=True)
        metadata = json.loads((self.target / METADATA).read_text(encoding="utf-8"))
        self.assertEqual(metadata["shell_origin"], "existing_personal_shell")
        self.assertEqual(metadata["template_role"], "reference_only")
        self.assertEqual(metadata["student_acceptance"], "not_recorded")
        for name, content in before.items():
            self.assertEqual((self.target / name).read_bytes(), content, name)
            self.assertEqual(metadata["shell_files_sha256_at_preparation"][Path(name).as_posix()], hashlib.sha256(content).hexdigest())
        self.assertNotEqual(metadata["shell_files_sha256_at_preparation"]["workbench/cli.py"],
                            metadata["template_files_sha256"]["workbench/cli.py"])

    def test_reference_template_update_keeps_existing_handoff_and_legacy_metadata(self):
        PREPARE(self.target)
        metadata_path = self.target / METADATA
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.pop("shell_origin")
        metadata.pop("template_role")
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        updated = self.base / "updated-reference-template"
        for name in PREPARE.__globals__["SHELL_FILES"]:
            destination = updated / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / "docs/courses/L00/starter" / name).read_bytes())
        (updated / "README.md").write_text("对照模板的新说明，不改变已登记的个人项目。\n", encoding="utf-8")
        before = self.snapshot(self.target)
        with patch.dict(PREPARE.__globals__, {"STARTER": updated}):
            result = PREPARE(self.target, continue_l01=True)
        self.assertEqual(result["created_files"], [])
        self.assertEqual(before, self.snapshot(self.target))

    def test_continue_refuses_conflicting_tool_before_any_write(self):
        PREPARE(self.target)
        (self.target / AIDS[0]).unlink()
        (self.target / METADATA).unlink()
        (self.target / AIDS[1]).write_text("本人不同的工具\n", encoding="utf-8")
        before = self.snapshot(self.target)
        with self.assertRaisesRegex(ValueError, "已有文件不同"):
            PREPARE(self.target, continue_l01=True)
        self.assertEqual(before, self.snapshot(self.target))

    def test_continue_refuses_invalid_metadata_and_missing_shell(self):
        PREPARE(self.target)
        metadata_path = self.target / METADATA
        metadata_path.write_text('{"student_acceptance":"accepted"}', encoding="utf-8")
        before = self.snapshot(self.target)
        with self.assertRaisesRegex(ValueError, "已有隔离记录"):
            PREPARE(self.target, continue_l01=True)
        self.assertEqual(before, self.snapshot(self.target))
        (self.target / "workbench/workbench_server.py").unlink()
        before = self.snapshot(self.target)
        with self.assertRaisesRegex(ValueError, "缺少必需文件"):
            PREPARE(self.target, continue_l01=True)
        self.assertEqual(before, self.snapshot(self.target))

    def _start_shell(self, extra_args=(), *, entry=("-m", "workbench.cli", "serve-workbench")):
        log_path = self.base / ("startup-" + str(len(list(self.base.glob("startup-*")))) + ".txt")
        stream = log_path.open("wb")
        process = subprocess.Popen([sys.executable, "-X", "utf8", *entry,
                                    "--port", "0", *extra_args], cwd=self.target, stdout=stream, stderr=stream)

        def stop():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            stream.close()

        self.addCleanup(stop)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"http://127\.0\.0\.1:(\d+)/", text)
            if match:
                return f"http://127.0.0.1:{match.group(1)}", process, text
            if process.poll() is not None:
                self.fail(f"实际启动已退出 {process.returncode}：{text}")
            time.sleep(0.05)
        self.fail(f"实际启动超时：{log_path.read_text(encoding='utf-8', errors='replace')}")

    def test_actual_http_shell_health_static_errors_and_no_database(self):
        PREPARE(self.target)
        url, process, _ = self._start_shell()
        opener = build_opener(ProxyHandler({}))
        with opener.open(url + "/api/health", timeout=3) as response:
            health = json.load(response)
        self.assertEqual(health["surface"], "workbench")
        self.assertEqual(health["capabilities"], ["shell"])
        self.assertEqual(health["database"], str(self.target / ".runtime/workbench/workbench.db"))
        self.assertFalse(health["database_exists"])
        self.assertNotIn("ready", health)
        self.assertNotIn("acceptance", health)
        with opener.open(url + "/", timeout=3) as response:
            self.assertIn("事项与决策", response.read().decode("utf-8"))
        for route, mime in (("/app.js?v=1", "text/javascript"), ("/styles.css", "text/css")):
            with self.subTest(route=route), opener.open(url + route, timeout=3) as response:
                self.assertIn(mime, response.headers["Content-Type"])
                self.assertGreater(len(response.read()), 0)
        for route in ("/missing", "/../README.md", "/api/v1/initiatives", "/api/health/ready"):
            with self.subTest(route=route), self.assertRaises(HTTPError) as raised:
                opener.open(url + route, timeout=3)
            self.assertEqual(raised.exception.code, 404)
        with self.assertRaises(HTTPError) as raised:
            opener.open(Request(url + "/api/v1/initiatives", data=b"", method="POST"), timeout=3)
        self.assertEqual(raised.exception.code, 501)
        self.assertIsNone(process.poll())
        self.assertFalse((self.target / ".runtime").exists())

    def test_explicit_runtime_and_reserved_arguments_do_not_create_capabilities(self):
        PREPARE(self.target)
        runtime = self.base / "explicit-runtime"
        url, _, _ = self._start_shell(("--runtime-dir", str(runtime)))
        with build_opener(ProxyHandler({})).open(url + "/api/health", timeout=3) as response:
            health = json.load(response)
        self.assertEqual(health["runtime"], str(runtime))
        self.assertFalse(runtime.exists())
        result = subprocess.run([sys.executable, "-X", "utf8", "-c",
                                 "from workbench.workbench_server import WorkbenchApp; "
                                 "import json; print(json.dumps(WorkbenchApp('unused', enable_code_execution=True, "
                                 "enable_advanced_runtime=True, erp_url='http://127.0.0.1:8000').health()))"],
                                cwd=self.target, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["capabilities"], ["shell"])
        self.assertFalse((self.target / "unused").exists())


if __name__ == "__main__":
    unittest.main()
