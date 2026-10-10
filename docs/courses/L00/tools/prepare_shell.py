"""Prepare the L00 shell and L01 teaching aids without replacing student work."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[4]
STARTER = Path(__file__).resolve().parents[1] / "starter"
CORE_SHELL_FILES = (
    "workbench/__init__.py", "workbench/cli.py", "workbench/runtime_paths.py",
    "workbench/workbench_server.py", "workbench_web/index.html",
    "workbench_web/app.js", "workbench_web/styles.css", "pyproject.toml", "README.md", ".gitignore",
)
SHELL_FILES = CORE_SHELL_FILES + (
    "main.py", "AGENTS.md", "agent/__init__.py", "eval/__init__.py", "harness_web/__init__.py",
    "scripts/.gitkeep", "deploy/.gitkeep", "docs/README.md", "docs/reference/README.md",
    "docs/reference/工作台具体设计.md", "tests/__init__.py", "tests/test_workbench_server.py",
)
OPTIONAL_SHELL_FILES = {"harness_web/__init__.py"}
TEACHING_TOOLS = ("docs/courses/L01/tools/evidence.py", "docs/courses/L01/tools/import_evidence.py")
METADATA = "lesson-01-submission/00-isolation.json"


def _check_path(path: Path) -> None:
    """Reject symlinks and Windows reparse points in any existing component."""
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise ValueError(f"拒绝符号链接或重解析目录：{component}")
        if component != path and not component.is_dir():
            raise ValueError(f"路径上级不是目录：{component}")


def _target_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("--target 必须是个人项目的绝对路径")
    _check_path(path)
    path = path.resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError("目标不能是课程参考仓库或其中的子目录")
    if path.exists() and not path.is_dir():
        raise ValueError("目标必须是目录")
    return path


def _read_file(path: Path) -> bytes:
    _check_path(path)
    if not path.is_file():
        raise ValueError(f"缺少必需文件：{path}")
    return path.read_bytes()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_shell(target: Path) -> dict[str, str]:
    # The previous ten-file project remains valid. Report missing structure
    # rather than silently writing an architecture or tests for its owner.
    hashes = {name: _digest(_read_file(target / name)) for name in CORE_SHELL_FILES}
    for name in SHELL_FILES:
        if name in hashes:
            continue
        destination = target / name
        _check_path(destination)
        if destination.exists():
            hashes[name] = _digest(_read_file(destination))
    for name, required in (("workbench/runtime_paths.py", {"service_runtime"}),
                           ("workbench/workbench_server.py", {"WorkbenchApp", "make_handler", "serve"}),
                           ("workbench/cli.py", {"main"})):
        try:
            source = (target / name).read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=name)
        except (SyntaxError, UnicodeError) as error:
            raise ValueError(f"壳源码不能解析：{name}：{error}") from error
        definitions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        if not required <= definitions:
            raise ValueError(f"壳缺少约定入口：{name}：{sorted(required - definitions)}")
        if name.endswith("cli.py") and "serve-workbench" not in source:
            raise ValueError("壳 CLI 缺少 serve-workbench 入口")
        if name.endswith("workbench_server.py"):
            app = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WorkbenchApp")
            if not any(isinstance(node, ast.FunctionDef) and node.name == "health" for node in app.body):
                raise ValueError("壳 WorkbenchApp 缺少 health 方法")
    return hashes


def prepare_shell(value: str | Path, *, continue_l01: bool = False) -> dict:
    target = _target_path(value)
    if continue_l01:
        if not target.is_dir():
            raise ValueError("承接目标不存在；首次创建时不要加 --continue-l01")
        shell_hashes = _validate_shell(target)
    else:
        if target.exists() and any(target.iterdir()):
            raise ValueError("首次创建只接受空目录；已有对齐壳请使用 --continue-l01")
        shell_hashes = {}

    template = {name: _read_file(STARTER / name) for name in SHELL_FILES}
    aids = {name: _read_file(ROOT / name) for name in TEACHING_TOOLS}
    template_hashes = {name: _digest(content) for name, content in template.items()}
    tool_hashes = {name: _digest(content) for name, content in aids.items()}
    actual_hashes = shell_hashes or template_hashes
    missing_layout = [name for name in SHELL_FILES if name not in OPTIONAL_SHELL_FILES and name not in actual_hashes]
    layout = ("aligned_directory_skeleton" if not missing_layout else
              "legacy_minimal" if set(actual_hashes) <= set(CORE_SHELL_FILES) | OPTIONAL_SHELL_FILES
              else "partial_directory_skeleton")
    planned = {} if continue_l01 else dict(template)
    planned.update(aids)

    # Preflight every destination before creating or changing anything.
    writes: dict[str, bytes] = {}
    for name, content in planned.items():
        destination = target / name
        _check_path(destination)
        if destination.exists():
            if not destination.is_file() or destination.read_bytes() != content:
                raise ValueError(f"已有文件不同，保留原文件并停止：{destination}")
        else:
            writes[name] = content
    metadata_path = target / METADATA
    _check_path(metadata_path)
    if metadata_path.exists():
        if not continue_l01 or not metadata_path.is_file():
            raise ValueError(f"不能覆盖已有隔离记录：{metadata_path}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            valid = (metadata["source_root"] == str(ROOT) and metadata["path"] == str(target)
                     and metadata["source"] == "personal_shell"
                     and metadata["student_start"]["lesson"] == 1
                     and metadata["student_start"]["source"] == "personal_shell"
                     and metadata["teaching_tools_sha256"] == tool_hashes)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"已有隔离记录无法确认，保留并停止：{metadata_path}") from error
        if not valid:
            raise ValueError(f"已有隔离记录与本次来源或目标不一致，保留并停止：{metadata_path}")
    else:
        metadata = {"schema_version": 1, "mode": "personal_shell",
                    "source": "personal_shell", "source_root": str(ROOT), "path": str(target),
                    "shell_origin": "existing_personal_shell" if continue_l01 else "course_template",
                    "template_role": "reference_only" if continue_l01 else "copied_scaffold",
                    "layout": layout, "missing_layout_files": missing_layout,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "student_start": {"lesson": 1, "source": "personal_shell"},
                    "template_files_sha256": template_hashes,
                    "shell_files_sha256_at_preparation": actual_hashes,
                    "teaching_tools_sha256": tool_hashes,
                    "student_acceptance": "not_recorded",
                    "boundary": ("shell_files_sha256_at_preparation 记录登记时个人项目源码；"
                                 "template_files_sha256 仅标识本次课程对照模板，不表示个人源码来自模板；"
                                 if continue_l01 else
                                 "shell_files_sha256_at_preparation 记录本次复制的课程壳；"
                                 "template_files_sha256 标识复制时的课程模板；")
                                + "teaching_tools_sha256 与 source_root 标识课程工具来源。"
                                  "缺少的目录骨架列在 missing_layout_files，不会补写个人设计或测试。"
                                  "不推断源码作者；未验证学生 Spec、红绿结果、自举或验收。"}
        writes[METADATA] = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    for name, content in writes.items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also refuses a file appearing after preflight.
        with destination.open("xb") as stream:
            stream.write(content)
    return {"mode": "continue_l01" if continue_l01 else "create_shell",
            "source_root": str(ROOT), "path": str(target), "source": "personal_shell",
            "metadata_path": str(metadata_path), "created_files": list(writes),
            "layout": layout, "missing_layout_files": missing_layout,
            "student_acceptance": "not_recorded"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="创建同目录工作台壳，或为已有壳补齐 L01 课程支架；不覆盖学生文件")
    parser.add_argument("--target", required=True, type=Path, help="参考仓库之外的个人项目绝对路径")
    parser.add_argument("--continue-l01", action="store_true", help="保留已有壳源码，只核对路径并补齐工具与来源记录")
    args = parser.parse_args(argv)
    try:
        result = prepare_shell(args.target, continue_l01=args.continue_l01)
    except (OSError, ValueError) as error:
        print(f"准备失败：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
