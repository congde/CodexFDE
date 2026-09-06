"""Project source snapshots and independent Eval in the candidate directory."""
import json
from pathlib import Path
import secrets
import subprocess

from .execution import _is_sensitive_path


def project_source_paths(root, runtime):
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=root, capture_output=True, check=True)
    paths = []
    names = set(result.stdout.decode('utf-8').split('\0')) - {''}
    if (root / 'AGENTS.md').is_file():
        names.add('AGENTS.md')
    for name in sorted(names):
        path = root / name
        if any(p in {'.venv', 'node_modules', '__pycache__', '.runtime', '.harness-runtime'} for p in Path(name).parts):
            continue
        if _is_sensitive_path(name) or (not root.is_relative_to(runtime) and path.resolve().is_relative_to(runtime)):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('项目源文件不可链接到其他位置：' + name)
        if path.is_file():
            paths.append(path)
    return paths


class CandidateProjectEval:
    def __init__(self, workspace, runtime, task_id, command, label):
        self.workspace, self.runtime = Path(workspace), Path(runtime)
        self.task_id, self.command, self.label = task_id, command, label

    def __call__(self, suite='blocking', write_report=True):
        from .execution_control import checkpoint
        checkpoint()
        folder = self.runtime / 'project-reports' / self.task_id / secrets.token_hex(12)
        folder.mkdir(parents=True)
        report_path = folder / 'report.json'
        command = [p.replace('{report_path}', str(report_path)).replace('{workspace}', str(self.workspace))
                   for p in self.command]
        from .execution import CodexExecutionRunner
        import time
        runner = CodexExecutionRunner(self.workspace, self.runtime)
        result = runner._run_codex_streaming(command, '', 1800, lambda line: None, time.monotonic())
        (folder / 'process.json').write_text(json.dumps({'command': command, 'cwd': str(self.workspace),
            'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}, ensure_ascii=False), encoding='utf-8')
        checkpoint()
        report = json.loads(report_path.read_text(encoding='utf-8') if report_path.exists() else result.stdout)
        summary = report.get('summary', {})
        if summary.get('decision') not in {'pass', 'block'}:
            raise RuntimeError('项目 Eval 报告缺少 pass/block 决策')
        if (result.returncode == 0) != (summary['decision'] == 'pass'):
            raise RuntimeError('项目 Eval 退出码与报告结论不一致')
        if summary['decision'] == 'pass' and (summary.get('blocking_failed', 0) or
                any(r.get('level') == 'blocking' and r.get('passed') is not True for r in report.get('results', []))):
            raise RuntimeError('项目 Eval 报告包含失败，不能声明通过')
        report['runner'] = {'workspace': str(self.workspace), 'process_returncode': result.returncode,
                            'validated': True, 'label': self.label, 'report_path': str(report_path)}
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        return report
