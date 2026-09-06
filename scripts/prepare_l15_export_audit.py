"""Create a local-only L15 rehearsal baseline with an actual missing CSV behavior.

No user checkout commit, feedback approval or product acceptance is performed.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from workbench.course_snapshot import prepare_source_snapshot


CASE = '''
def inventory_empty_export_retains_schema() -> str:
    import csv
    import io
    from flowerp.models import PermissionDenied
    with tempfile.TemporaryDirectory(prefix="empty-export-") as directory:
        store = ERPStore(Path(directory) / "erp.db")
        export = ImportExportService(store)
        headers = ["sku", "name", "site", "location", "lot_id", "on_hand", "reserved", "available"]
        content = export.export_csv(SYSTEM_PRINCIPAL, "inventory")
        assert list(csv.reader(io.StringIO(content.lstrip("\\ufeff")))) == [headers], "空库存导出必须保留列名"
        service = ERPService(store)
        service.add_product("EXPORT-EMPTY-A", "导出检查", 100, 1)
        service.receive_stock("EXPORT-EMPTY-A", 7, "empty-export-receipt")
        before = store.rows("SELECT * FROM stock_balance ORDER BY product_id")
        content = export.export_csv(SYSTEM_PRINCIPAL, "inventory")
        rows = list(csv.DictReader(io.StringIO(content.lstrip("\\ufeff"))))
        assert len(rows) == 1 and list(rows[0]) == headers
        assert rows[0]["sku"] == "EXPORT-EMPTY-A" and rows[0]["available"] == "7"
        denied = Principal("reader", "ORG-DEFAULT", "reader", "只读测试", frozenset())
        try:
            export.export_csv(denied, "inventory")
        except PermissionDenied:
            pass
        else:
            raise AssertionError("无报告权限不得导出")
        try:
            export.export_csv(SYSTEM_PRINCIPAL, "unknown-export-type")
        except ValidationError:
            pass
        else:
            raise AssertionError("未知导出类型必须拒绝")
        other = Principal("other", "ORG-OTHER", "other", "另一个组织", frozenset({"reports.read"}))
        assert list(csv.reader(io.StringIO(export.export_csv(other, "inventory").lstrip("\\ufeff")))) == [headers]
        assert store.rows("SELECT * FROM stock_balance ORDER BY product_id") == before, "导出不得改变库存"
        return "空库存保留列名；正常数量、权限、组织隔离及失败后不变状态成立"
'''


def prepare(repository, runtime):
    runtime = Path(runtime).resolve()
    if runtime.exists():
        raise FileExistsError('使用新的运行目录，以保留既有证据')
    snapshot = prepare_source_snapshot(repository, runtime, 15, 'TASK-L15-EMPTY-EXPORT-BASE')
    root = Path(snapshot['path'])
    def git(*args):
        return subprocess.run(['git', *args], cwd=root, check=True, text=True,
                              encoding='utf-8', capture_output=True).stdout.strip()
    git('tag', 'course/l15-start', snapshot['snapshot_commit'])
    cases = root / 'eval/cases.py'
    cases.write_text(cases.read_text(encoding='utf-8') + CASE, encoding='utf-8', newline='\n')
    harness = root / 'eval/harness.py'
    text = harness.read_text(encoding='utf-8')
    marker = 'EVALS: list[tuple[str, str, Callable[[], str]]] = ['
    if text.count(marker) != 1:
        raise ValueError('Eval 注册入口已变化')
    text = text.replace(marker, marker + '\n    ("inventory_empty_export_retains_schema", "blocking", cases.inventory_empty_export_retains_schema),')
    harness.write_text(text, encoding='utf-8', newline='\n')
    git('add', 'eval/cases.py', 'eval/harness.py')
    git('-c', 'user.name=Maintainer rehearsal', '-c', 'user.email=rehearsal@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Local rehearsal: add empty inventory export acceptance')
    payload = {'repository':str(root), 'runtime':str(runtime), 'baseline_commit':snapshot['snapshot_commit'],
               'session_commit':git('rev-parse','HEAD'), 'new_case':'inventory_empty_export_retains_schema',
               'student_achievement':False, 'human_feedback_approval':False, 'published_course_baseline':False,
               'eval_sha256':{p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (cases,harness)}}
    (runtime / 'preparation.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    spec = '''## 来源
维护者实际观察：空库存调用 ImportExportService.export_csv(..., "inventory") 返回空字符串。
这是本地工程链路验证，不声称真实学生反馈已被人审采用。
## 目标
库存为空时仍返回带固定列名的 CSV，让使用者能确认导出结构。
## 非目标
不修改库存数量、其他导出类型、界面、权限或数据库结构。
## 约束
仅修改 flowerp/import_export.py 的库存导出行为；不得改动 eval/ 中的已冻结验收。
保持已有非空导出、权限检查、组织过滤和未知类型拒绝行为。
使用仓库现有 Python 标准库；不安装依赖，不提交代码，不修改运行库。
## 验收用例
inventory_empty_export_retains_schema：空库存只有固定列名行；非空数量正确；无权限和未知类型拒绝；其他组织看不到库存；导出前后库存一致。
固定列名顺序：sku,name,site,location,lot_id,on_hand,reserved,available。
## 完成定义
新增验收先失败，修改后通过；保留范围内改动和独立复验，停在 review，不代替人审。
'''
    (runtime / 'requirement.md').write_text(spec,encoding='utf-8',newline='\n')
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(Path.cwd(), args.runtime),ensure_ascii=False,indent=2))
