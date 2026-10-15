from __future__ import annotations

import json
import logging
import mimetypes
import sqlite3
import uuid
import sys
from .project_store import ProjectStore
from .project_registration import ProjectRegistration
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .cockpit import current_course, last_upgrade, lesson_eval_runner, lesson_number_from_requirement
from .course_mainline import lesson_contract, validate_mainline, write_lesson_spec
from .course_verification import CourseVerificationNotReady, prepare_verification, target_verification_key
from .delivery_view import DeliveryViewService
from .evolution import EvolutionStore
from .http_bind import create_http_server
from .http_origin import local_request_error
from .task_store import TaskStore, TaskSubmissionConflict
from .automation import DeliveryAutomation
from .workflow import run_task
from .web_execution import WebExecution
from .runtime_lease import WorkbenchRuntimeLease
from .initiative import InitiativeStore
from .initiative import InitiativeSubmissionConflict
from .initiative_home import InitiativeHome
from .maintenance import MaintenanceGate, MaintenanceBusy
from .workbench_backup import BackupService, RESTORE_MARKER
from .initiative_workflow import InitiativeWorkflow
from .workflow_graph import WorkflowGraph, WorkflowConflict
from .mutation_receipts import MutationPending
from .http_reliability import RequestBodyTimeout, WorkbenchHTTPServer, finish_rejected_response, read_request_body


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = (ROOT / "workbench_web").resolve()


class WorkbenchApp:
    """Required follow-along cockpit. Owns workbench.db only."""

    def __init__(self, runtime_dir: str | Path = ".runtime", *, port: int = 8001, eval_factory=None,
                 enable_code_execution=False, erp_url='http://127.0.0.1:8000', enable_advanced_runtime=False) -> None:
        target = urlparse(erp_url)
        if (target.scheme != 'http' or target.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or target.username or target.password or target.query or target.fragment
                or target.path not in {'', '/'} or (target.port or 80) == port):
            raise ValueError('客户项目地址必须是独立的本机 HTTP 服务地址')
        self.erp_url = erp_url.rstrip('/')
        self.runtime = Path(runtime_dir).resolve()
        from .reference_paths import load_reference_mappings
        load_reference_mappings(self.runtime)
        if (self.runtime / 'workbench-restore-failed.json').exists():
            raise ValueError('工作台恢复尚未完成。请保留失败目录和备份，校验后重新恢复到空的原路径，再启动服务。')
        database = self.runtime / 'workbench.db'
        history_markers = ('sessions_jsonl', RESTORE_MARKER,
                           'workbench.db-wal', 'workbench.db-shm', 'workbench.db-journal')
        if not database.is_file() and any((self.runtime / name).exists() for name in history_markers):
            raise ValueError('已使用的工作台运行目录缺少原数据库。请保留现场并恢复原库，不能自动创建空库替代历史。')
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.port = port
        self.service_instance = uuid.uuid4().hex
        self.eval_factory = eval_factory
        self.tasks = TaskStore(self.runtime / "workbench.db")
        with self.tasks.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workbench_instance (singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO workbench_instance(singleton,id) VALUES(1,?)', (uuid.uuid4().hex,))
            self.runtime_instance = db.execute('SELECT id FROM workbench_instance WHERE singleton=1').fetchone()['id']
        self.graphs = WorkflowGraph(self.tasks)
        self.projects = ProjectStore(self.tasks.path)
        self.project_registration = ProjectRegistration(self.projects)
        existing = next((p for p in self.projects.list() if Path(p['root_path']) == ROOT), None)
        default = existing or self.projects.create('CodexFDE 工作台', ROOT,
            [str(ROOT / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')),
             '-X', 'utf8', '-m', 'eval.harness', '--suite', 'blocking', '--report-path', '{report_path}'],
            project_id='PROJECT-FLOWERP')
        self.initiatives = InitiativeStore(self.tasks.path)
        self.initiative_home = InitiativeHome(self.tasks.path)
        self.backups = BackupService(self.runtime)
        with self.initiatives.connect() as db:
            db.execute("UPDATE initiatives SET project_id=? WHERE project_id IN ('', 'FlowERP')", (default['id'],))
        # Old unbound records belong to the original course checkout. A new
        # default applies to newly created initiatives only.
        self.default_project = (self.projects.default() or default)['id']
        self.evolutions = EvolutionStore(self.tasks.path)
        self.views = DeliveryViewService(self.tasks, self.evolutions, self.graphs)
        self.initiative_workflow = InitiativeWorkflow(ROOT, self.runtime, self.initiatives, self.tasks,
                                                     enabled=enable_code_execution, projects=self.projects)
        from .project_configuration import ProjectConfiguration
        from .project_plan import ProjectPlans
        from .generic_preview import ProjectPreviews
        from .deployment import Deployments
        from .mutation_receipts import MutationReceipts
        from .delivery_runtime import DeliveryRuntime
        self.configurations = ProjectConfiguration(self.projects)
        self.project_plans = ProjectPlans(self.projects, self.initiatives, self.initiative_workflow)
        self.previews = ProjectPreviews(self.runtime, self.tasks, self.projects, self.configurations)
        self.deployments = Deployments(self.runtime, self.tasks, self.projects, self.initiative_workflow, self.configurations)
        self.mutations = MutationReceipts(self.tasks)
        self.delivery_runtime = DeliveryRuntime(ROOT, self.runtime, self.tasks, self.projects, self.initiatives, self.initiative_workflow)
        (self.runtime / 'sessions_jsonl').mkdir(exist_ok=True)
        self.initiative_workflow.delivery_runtime = self.delivery_runtime
        self.initiative_workflow.submitter = self.delivery_runtime.submit_daily
        self.initiative_workflow.require_preflight = True
        self.code = WebExecution(ROOT, self.runtime, self.tasks, enabled=enable_code_execution,
                                 daily_workflow=self.initiative_workflow, delivery_runtime=self.delivery_runtime)
        self.initiative_workflow.project_plans = self.project_plans
        self.advanced_enabled = enable_advanced_runtime
        self.harness_api = None
        if enable_advanced_runtime:
            from .platform_api import HarnessPlatformAPI
            self.harness_api = HarnessPlatformAPI(self.runtime, ROOT, projects=self.projects, tasks=self.tasks,
                initiatives=self.initiatives, runtime_store=self.delivery_runtime.sessions,
                delivery_runtime=self.delivery_runtime)
        self.automation = DeliveryAutomation(
            self.tasks, self.runtime, max_attempts=1,
            agent_runner=lambda task_id, actor: self._run_course_task(task_id, actor),
        )

    def _run_course_task(self, task_id: str, actor: str) -> dict:
        self.verify_task(task_id, actor)
        return self.tasks.get(task_id)

    def health(self) -> dict:
        from .service_health import readiness
        ready_status, ready = readiness(self.runtime, self.runtime_instance)
        return {
            "status": "ok" if ready_status == 200 else "unavailable",
            "ready": ready_status == 200,
            "checks": ready['checks'],
            "surface": "workbench",
            "workbench_port": self.port,
            "erp_url": self.erp_url,
            "runtime": str(self.runtime),
            "runtime_instance": self.runtime_instance,
            "service_instance": self.service_instance,
            "advanced_runtime": self.advanced_enabled,
            "restored": (self.runtime / RESTORE_MARKER).is_file(),
            "database": str(self.runtime / "workbench.db"),
            "erp_database": None,
            "message": f"这是个人研发工作台。FlowERP 是客户项目案例，请在 {self.erp_url} 打开。",
            "thesis": {
                "workbench": "研发工作台是写代码的主体",
                "flowerp": "FlowERP 是验证场，证明工作台有效且能自迭代",
                "codex": "Codex 是底座，既造工作台也被工作台约束",
            },
        }

    def backup_busy(self):
        with self.initiative_workflow.lock:
            workflow_busy = any(t.is_alive() for t in self.initiative_workflow.workers.values())
        with self.automation._lock:
            automation_busy = bool(self.automation._pending) or any(t.is_alive() for t in self.automation._threads.values())
        with self.code.lock:
            code_busy = any(plan['state'] == 'starting' for plan in self.code.plans.values())
        preview_busy = self.previews.busy()
        return workflow_busy or automation_busy or code_busy or preview_busy or self.deployments.busy() or self.initiative_workflow.learning_generation.busy() or self.delivery_runtime.busy()

    def course_current(self, lesson: int | None = None) -> dict:
        return current_course(lesson, tasks=self.tasks.list(50))

    def upgrade(self) -> dict:
        return last_upgrade(self.evolutions)

    def course_verification(self, lesson: int, project_id=None, *, target=None) -> dict:
        return prepare_verification(ROOT, self.projects, lesson, project_id, target=target,
                                    injected_runner=self.eval_factory is not None)

    def create_course_task(self, lesson: int, request: str, actor: str, *, submission_key: str | None = None, business_refs=None, project_id=None, verification_key=None) -> dict:
        if not 4 <= int(lesson) <= 16:
            raise ValueError("Web 受控任务只允许选择 L04-L16")
        request = str(request).strip()
        actor = str(actor).strip()
        if not request:
            raise ValueError("现场需求不能为空")
        if len(request) > 1000:
            raise ValueError("现场需求不能超过 1000 个字符")
        if not actor:
            raise ValueError("必须填写具名提交人")
        if len(actor) > 80:
            raise ValueError("提交人名称不能超过 80 个字符")

        contract = lesson_contract(int(lesson))
        if business_refs is None:
            business_refs = []
        if not isinstance(business_refs, list) or len(business_refs) > 20 or any(
                not isinstance(ref, str) or not ref.strip() or len(ref) > 200 for ref in business_refs):
            raise ValueError('业务引用必须是最多 20 个非空编号，每个不超过 200 字符')
        refs = list(dict.fromkeys([*contract.business_refs, *(ref.strip() for ref in business_refs)]))
        # Recover a known receipt before rechecking mutable environment settings.
        # The target is already stored atomically in the original task event.
        if submission_key:
            with self.tasks.connect() as db:
                receipt = db.execute('SELECT task_id FROM task_submissions WHERE submission_key=?',
                                     (submission_key,)).fetchone()
            if receipt:
                original = self.tasks.get(receipt['task_id'])
                frozen = next(((event.get('evidence') or {}).get('verification_target')
                               for event in original.get('events', [])
                               if (event.get('evidence') or {}).get('verification_target')), None)
                if frozen:
                    if (project_id and project_id != frozen.get('project_id')) or (
                            verification_key is not None and verification_key != target_verification_key(lesson, frozen)):
                        raise TaskSubmissionConflict('原提交编号已绑定另一个检查对象，请核对原任务。')
                    if frozen.get('product_root'):
                        import hashlib
                        refs.extend(['PROJECT:' + (frozen.get('project_id') or 'FLOWERP-ENV'),
                                     'SOURCE:' + hashlib.sha256(frozen['product_root'].encode('utf-8')).hexdigest()])
                    replay = self.tasks.create(request, contract.requirement_id, list(dict.fromkeys(refs)),
                        original['spec_path'], actor, automation_mode='automatic', execution_mode='verify',
                        write_scope=list(contract.write_scope), submission_key=submission_key,
                        verification_target=frozen)
                    return self.views.get(replay['id'])
        verification = self.course_verification(lesson, project_id)
        if not verification['ready']:
            raise CourseVerificationNotReady(verification)
        if verification_key is not None and verification_key != verification['verification_key']:
            verification.update(ready=False, message='检查对象或范围已变化。请重新核对条件后再提交。')
            verification['issues'].append({'code': 'verification_changed', 'message': verification['message'],
                                           'action': '点击“重新核对条件”，确认最新源码目录和检查项。'})
            raise CourseVerificationNotReady(verification)
        target = verification['target']
        if target.get('product_root'):
            import hashlib
            refs.extend(['PROJECT:' + (target.get('project_id') or 'FLOWERP-ENV'),
                         'SOURCE:' + hashlib.sha256(target['product_root'].encode('utf-8')).hexdigest()])
            refs = list(dict.fromkeys(refs))
        spec_path = self.runtime / "course" / f"L{int(lesson):02d}" / uuid.uuid4().hex / "FDE_SPEC.md"
        write_lesson_spec(int(lesson), spec_path)
        try:
            task = self.tasks.create(
                request=request,
                requirement_id=contract.requirement_id,
                business_refs=refs,
                spec_path=str(spec_path),
                actor=actor,
                execution_mode="verify",
                write_scope=list(contract.write_scope),
                automation_mode="automatic" if submission_key else "manual",
                submission_key=submission_key,
                verification_target=target,
            )
        except TaskSubmissionConflict:
            spec_path.unlink()
            spec_path.parent.rmdir()
            raise
        if task["spec_path"] != str(spec_path):
            # This attempt owns only the unused newly-generated file.
            spec_path.unlink()
            spec_path.parent.rmdir()
            return self.views.get(task["id"])
        self.tasks.append_event(
            task['id'], '课程复验对象已冻结', actor=actor, evidence=target,
        )
        self.tasks.append_event(
            task["id"],
            "Web 已创建受控课程任务；尚未授权 Codex 写入",
            actor=actor,
            evidence={"lesson": int(lesson), "execution_mode": "verify"},
        )
        return self.views.get(task["id"])

    def accept_course_task(self, lesson: int, request: str, actor: str, key: str, business_refs=None, project_id=None, verification_key=None) -> dict:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("异步提交必须提供 Idempotency-Key")
        created = self.create_course_task(lesson, request, actor, submission_key=key.strip(), business_refs=business_refs, project_id=project_id, verification_key=verification_key)
        task_id = created["task_id"]
        if self.tasks.get(task_id)["status"] == "queued":
            self.automation.start(task_id, actor=actor)
        return {"task_id": task_id, "accepted": True, "execution_mode": "verify",
                "status_url": f"/api/v1/tasks/{task_id}",
                "view_url": f"/api/v1/delivery/views/{task_id}"}

    def verify_task(self, task_id: str, actor: str) -> dict:
        actor = str(actor).strip()
        if not actor:
            raise ValueError("必须填写具名复验人")
        if len(actor) > 80:
            raise ValueError("复验人名称不能超过 80 个字符")
        task = self.tasks.get(task_id)
        if task.get('authorization_policy') == 'v0':
            if task.get('execution_mode') != 'verify':
                raise ValueError('编码任务须在关联事项中重新确认授权')
            run_task(self.tasks, task_id, actor)
            return self.views.get(task_id)
        if task.get('execution_mode') == 'codex':
            raise ValueError("代码任务须通过课程执行方案处理，不能从仅复验入口运行")
        status = str(task.get("status") or "")
        if status not in {"queued", "spec_ready", "rework"}:
            raise ValueError(f"当前状态 {status} 不能再跑复验")
        lesson = lesson_number_from_requirement(str(task.get("requirement_id") or ""))
        if lesson is None:
            run_task(self.tasks, task_id, actor,
                     suite_runner=(self.eval_factory or lesson_eval_runner)(None))
            return self.views.get(task_id)
        target = next((e.get('evidence') for e in reversed(task.get('events', []))
                       if e.get('detail') == '课程复验对象已冻结'), None)
        target = target or next(((e.get('evidence') or {}).get('verification_target')
                                for e in task.get('events', [])
                                if (e.get('evidence') or {}).get('verification_target')), None)
        verification = self.course_verification(lesson, target=target)
        if not verification['ready']:
            raise CourseVerificationNotReady(verification)
        if target is None:
            target = verification['target']
            self.tasks.append_event(task_id, '课程复验对象已冻结', actor=actor, evidence=target)
        runner = (self.eval_factory(lesson) if self.eval_factory else
                  lesson_eval_runner(lesson, product_root=target.get('product_root')))
        def bound_runner(*args, **kwargs):
            report = runner(*args, **kwargs)
            report['verification_target'] = target
            return report
        run_task(self.tasks, task_id, actor, suite_runner=bound_runner)
        return self.views.get(task_id)

    def submit_and_verify(self, lesson: int, request: str, actor: str, project_id=None, verification_key=None) -> dict:
        created = self.create_course_task(lesson, request, actor, project_id=project_id, verification_key=verification_key)
        return self.verify_task(created["task_id"], actor)

    def review_task(self, task_id: str, reviewer: str, decision: str, note: str) -> dict:
        task = self.tasks.get(task_id)
        if any(event.get('detail') == '网页具名授权日常研发' and
               (event.get('evidence') or {}).get('initiative_id') for event in task.get('events', [])):
            raise ValueError('请回到关联事项中核对当前候选并验收，或留下修改意见')
        daily = any(event.get('detail') == '网页具名授权日常研发' for event in task.get('events', []))
        if daily and decision == 'approve':
            packages = [event.get('evidence', {}) for event in task.get('events', [])
                        if event.get('detail') == '日常研发交付包已保存']
            if not packages or packages[-1].get('status') != 'review':
                raise ValueError('日常研发检查与交付包尚未完成，不能批准')
        if task.get('execution_mode') == 'codex' and decision == 'approve' and not daily and task.get('authorization_policy') != 'v0':
            gates = [event.get('evidence', {}) for event in task.get('events', [])
                     if event.get('detail') == '课程红绿差分判定已完成']
            if not gates or gates[-1].get('accepted') is not True:
                raise ValueError("课程红绿差分尚未通过，不能批准代码交付")
        self.tasks.review(task_id, str(reviewer), str(decision), str(note))
        return self.views.get(task_id)


def make_handler(app: WorkbenchApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Workbench/0.1"

        def _json(self, status: int, body: object) -> None:
            incomplete = self.command == 'POST' and not getattr(self, '_body_consumed', False)
            if incomplete:
                self.close_connection = True
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Workbench-Request-ID', self._request_id())
            self.send_header('X-Workbench-Service-Instance', app.service_instance)
            self.send_header('X-Workbench-Runtime-Instance', app.runtime_instance)
            if incomplete:
                self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(data)
                if incomplete:
                    finish_rejected_response(self)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                # The operation may already be committed. Never repeat it.
                self.close_connection = True

        def _request_id(self):
            if not getattr(self, 'request_id', None):
                self.request_id = uuid.uuid4().hex
            return self.request_id

        def _unavailable(self, error, *, status=503, kind='storage_unavailable'):
            if isinstance(error, (BrokenPipeError, ConnectionResetError)):
                self.close_connection = True
                return
            request_id = self._request_id()
            logging.getLogger('workbench.http').error(json.dumps({
                'request_id': request_id, 'method': self.command,
                'path': urlparse(self.path).path, 'error_type': type(error).__name__}), exc_info=True)
            return self._json(status, {'error': kind,
                'message': '工作台暂时无法完成操作，请保留输入并先读取原记录核对结果。',
                'request_id': request_id, 'automatic_replay': False,
                'result_unknown': self.command == 'POST'})

        def do_GET(self) -> None:  # noqa: N802
            try:
                rejected = local_request_error(self.headers, self.server.server_port)
                if rejected:
                    return self._json(403, rejected)
                path = urlparse(self.path).path
                if path in {'/api/health/live','/api/v1/initiatives/home','/api/v1/initiatives'} or path.startswith('/api/v1/backups') or not path.startswith('/api/'):
                    return self._get()
                with MaintenanceGate(app.runtime).write():
                    return self._get()
            except MaintenanceBusy as exc:
                return self._json(503, {'error': 'maintenance_busy', 'message': str(exc)})
            except (sqlite3.Error, OSError) as exc:
                return self._unavailable(exc)
            except Exception as exc:
                return self._unavailable(exc, status=500, kind='internal_error')

        def _get(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if (path.startswith('/api/') and path not in {'/api/health', '/api/health/live', '/api/health/ready'}
                        and not path.startswith('/api/v1/backups')):
                    from .service_health import readiness
                    _, ready = readiness(app.runtime, app.runtime_instance)
                    if not ready['checks']['database']['ok'] or not ready['checks']['recovery']['ok']:
                        return self._json(503, {'error': 'service_unavailable',
                            'message': '工作台数据不可用，请保留输入并先核对恢复状态。',
                            'automatic_replay': False})
                from .platform_http import get as platform_get
                extra = platform_get(app, path, query)
                if extra is not None:
                    return self._json(*extra)
                if path == "/api/health":
                    health = app.health()
                    return self._json(200 if health['ready'] else 503, health)
                if path == '/api/health/live':
                    return self._json(200, {'status': 'ok', 'surface': 'workbench'})
                if path == '/api/health/ready':
                    from .service_health import readiness
                    return self._json(*readiness(app.runtime, app.runtime_instance))
                if path == '/api/v1/backups':
                    return self._json(200, app.backups.list())
                if path.startswith('/api/v1/backups/'):
                    parts = path.split('/')
                    if len(parts) == 6 and parts[-1] == 'download':
                        archive = app.backups.archive_path(parts[-2])
                        size = archive.stat().st_size
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/zip')
                        self.send_header('Content-Length', str(size))
                        self.send_header('Content-Disposition', 'attachment; filename="' + archive.name + '"')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.end_headers()
                        with archive.open('rb') as source:
                            while chunk := source.read(1024 * 1024):
                                self.wfile.write(chunk)
                        return
                    if len(parts) == 5:
                        return self._json(200, app.backups.get(parts[-1]))
                    return self._json(404, {'error': 'not_found'})
                if path == '/api/v1/projects':
                    return self._json(200, {'items': app.projects.list(),
                        'default_project': (app.projects.default() or {'id': app.default_project})['id'],
                        'registration': {'sources': ['local', 'git'], 'eval_optional': True}})
                if path in {'/api/v1/initiatives', '/api/v1/initiatives/home'}:
                    if set(query) - {'project_id','group','q','include_mock','include_hidden','limit','cursor'}:
                        raise ValueError('事项查询包含未知参数')
                    filters = {k: v[0] for k, v in query.items()}
                    return self._json(200, app.initiative_home.page(**filters) if path.endswith('/home')
                                      else app.initiatives.query(**filters))
                if path.startswith('/api/v1/initiatives/'):
                    if path.endswith('/workflow'):
                        return self._json(200, app.initiative_workflow.get(path.split('/')[-2]))
                    return self._json(200, app.initiatives.get(path.rsplit('/', 1)[-1]))
                if path == "/api/v1/delivery/capabilities":
                    return self._json(200, {"surface": "workbench", "async_submission": True,
                                            "execution_modes": ["verify"], "requires_idempotency_key": True,
                                            "code_execution": "reviewable_plan" if app.code.enabled else "explicit_course_cli_only",
                                            "web_code_execution": app.code.enabled,
                                            "code_readiness": app.code.readiness(),
                                            "input_limits": {'v0_spec_chars': 24000, 'v0_request_bytes': 524288}})
                if path.startswith('/api/v1/execution/plans/'):
                    return self._json(200, app.code.get(path.rsplit('/', 1)[-1]))
                if path == "/api/course/current":
                    raw = (query.get("lesson") or [""])[0]
                    lesson = int(raw) if str(raw).isdigit() else None
                    return self._json(200, app.course_current(lesson))
                if path == '/api/course/verification':
                    if set(query) - {'lesson', 'project_id'}:
                        raise ValueError('课程复验查询包含未知参数')
                    return self._json(200, app.course_verification(
                        int((query.get('lesson') or ['0'])[0]),
                        (query.get('project_id') or [None])[0]))
                if path == "/api/cockpit/upgrade":
                    return self._json(200, app.upgrade())
                if path == "/api/course/status":
                    return self._json(200, validate_mainline(ROOT))
                if path in {"/api/tasks", "/api/v1/tasks"}:
                    limit = int((query.get("limit") or ["30"])[0])
                    return self._json(200, {"items": app.tasks.list(limit)})
                if path.startswith("/api/v1/tasks/"):
                    if path.endswith('/graph'):
                        task_id = path.split('/')[-2]
                        app.graphs.ensure(task_id)
                        return self._json(200, app.graphs.view(task_id))
                    if '/artifacts/' in path:
                        import hashlib
                        parts = path.split('/')
                        if len(parts) != 8 or parts[5] != 'artifacts' or parts[7] not in {'stdout', 'stderr', 'diff'}:
                            raise ValueError('证据请求格式无效')
                        task = app.tasks.get(parts[4])
                        event = next((e for e in task['events'] if str(e['id']) == parts[6]), None)
                        evidence = (event or {}).get('evidence') or {}
                        artifact = evidence.get('artifacts', {}).get(parts[7])
                        if not artifact:
                            raise ValueError('本轮没有该证据文件')
                        file_path = Path(artifact).resolve()
                        if not file_path.is_relative_to(app.runtime / 'delivery' / task['id']) or not file_path.is_file():
                            raise ValueError('证据不在本任务运行目录内')
                        raw = file_path.read_bytes()
                        if hashlib.sha256(raw).hexdigest() != evidence.get('artifact_sha256', {}).get(parts[7]):
                            raise ValueError('证据内容已变化，不能作为原始记录展示')
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/plain; charset=utf-8')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.send_header('Content-Length', str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                    if path.endswith('/patch'):
                        import hashlib
                        task = app.tasks.get(path.split('/')[-2])
                        packages = [event.get('evidence', {}) for event in task['events']
                                    if event.get('detail') == '日常研发交付包已保存']
                        if not packages:
                            raise ValueError('本任务尚未生成交付补丁')
                        package = packages[-1]
                        file_path = Path(package['patch_path']).resolve()
                        if not file_path.is_relative_to(app.runtime / 'daily-delivery') or not file_path.is_file():
                            raise ValueError('补丁文件不可用')
                        data = file_path.read_bytes()
                        if hashlib.sha256(data).hexdigest() != package['patch_sha256']:
                            raise ValueError('补丁内容已变化，请核对原始交付证据')
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Disposition', f'attachment; filename="{task["id"]}.patch"')
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    return self._json(200, app.tasks.get(path.rsplit("/", 1)[-1]))
                if path == "/api/v1/delivery/views":
                    limit = int((query.get("limit") or ["20"])[0])
                    return self._json(200, app.views.list(limit))
                if path.startswith("/api/v1/delivery/views/"):
                    return self._json(200, app.views.get(path.rsplit("/", 1)[-1]))
                if path == '/api/v1/workflow-handlers':
                    return self._json(200, {'items': app.graphs.handlers()})
                relative = "index.html" if path == "/" else path.lstrip("/")
                file_path = (WEB_ROOT / relative).resolve()
                if WEB_ROOT not in file_path.parents and file_path != WEB_ROOT:
                    return self._json(404, {"error": "not_found"})
                if not file_path.is_file():
                    return self._json(404, {"error": "not_found"})
                data = file_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(file_path)[0] or "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
            except KeyError as exc:
                self._json(404, {"error": "not_found", "message": str(exc)})
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": type(exc).__name__, "message": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            self._body_consumed = False
            try:
                rejected = local_request_error(self.headers, self.server.server_port, write=True)
                if rejected:
                    return self._json(403, rejected)
                if urlparse(self.path).path.startswith('/api/v1/backups') or urlparse(self.path).path == '/api/v1/migration/prepare':
                    return self._post()
                with MaintenanceGate(app.runtime).write():
                    return self._post()
            except MaintenanceBusy as exc:
                return self._json(503, {'error': 'maintenance_busy', 'message': str(exc)})
            except RequestBodyTimeout:
                return self._json(408, {'error': 'request_timeout', 'message': '请求体读取超时；本次操作未执行。',
                                       'automatic_replay': False})
            except (sqlite3.Error, OSError) as exc:
                return self._unavailable(exc)
            except Exception as exc:
                return self._unavailable(exc, status=500, kind='internal_error')

        def _post(self) -> None:
            try:
                path = urlparse(self.path).path
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    return self._json(415, {"error": "unsupported_media_type", "message": "只接受 application/json"})
                if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                    self.close_connection = True
                    return self._json(400, {'error': 'invalid_body', 'message': '必须提供唯一明确的 Content-Length'})
                length = int(self.headers.get("Content-Length", "0"))
                parts = path.split('/')
                is_v0 = len(parts) == 7 and parts[:4] == ['', 'api', 'v1', 'initiatives'] and parts[-2:] == ['workflow', 'v0']
                maximum = 524288 if is_v0 else 131072 if path in {'/api/v1/execution/plans', '/api/v1/execution/daily-plans'} or '/runtime/' in path or path.startswith('/api/v1/projects') else 32768 if path.startswith('/api/v1/initiatives') else 8192
                if length <= 0 or length > maximum:
                    return self._json(400, {"error": "invalid_body", "message": "请求体为空或过大"})
                raw = read_request_body(self, length, timeout=getattr(self.server, 'body_timeout', 10))
                body = json.loads(raw.decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("请求体必须是 JSON 对象")
                expected_service = self.headers.get('X-Workbench-Expected-Service-Instance')
                expected_runtime = self.headers.get('X-Workbench-Expected-Runtime-Instance')
                if ((expected_service and expected_service != app.service_instance) or
                        (expected_runtime and expected_runtime != app.runtime_instance)):
                    return self._json(409, {'error':'instance_changed', 'message':'工作台服务或数据库实例变化，请重新载入核对后决定；本次操作未执行。', 'automatic_replay':False})
                from .service_health import readiness
                ready_status, ready = readiness(app.runtime, app.runtime_instance)
                safe_stop = ((path.endswith('/runtime/control') and body.get('action') in {'pause', 'cancel'}
                              or path.endswith('/workflow/cancel'))
                             and ready['checks']['database']['ok'] and ready['checks']['recovery']['ok'])
                if ready_status != 200 and not safe_stop:
                    return self._json(503, {'error': 'service_unavailable',
                        'message': '工作台数据或运行目录尚未就绪，请保留输入并查看就绪检查。',
                        'checks': ready['checks'], 'automatic_replay': False, 'result_unknown': False})
                from .platform_http import post as platform_post
                extra = platform_post(app, path, body, self.headers, self.client_address[0])
                if extra is not None:
                    return self._json(*extra)
                if path.startswith('/api/v1/backups'):
                    if self.client_address[0] not in {'127.0.0.1', '::1'}:
                        return self._json(403, {'error': 'cross_origin', 'message': '请从本机工作台操作备份'})
                    actor = app.initiative_workflow.actor(body.get('actor'))
                    if path == '/api/v1/backups':
                        return self._json(201, app.backups.create(actor, busy_check=app.backup_busy))
                    parts = path.split('/')
                    if len(parts) == 6 and parts[-1] == 'verify':
                        return self._json(200, app.backups.verify(app.backups.archive_path(parts[-2])))
                    return self._json(404, {'error': 'not_found'})
                if path == '/api/v1/projects' or path.startswith('/api/v1/projects/'):
                    if self.client_address[0] not in {'127.0.0.1', '::1'}:
                        raise ValueError('项目登记只允许从本机工作台操作')
                    app.initiative_workflow.actor(body.get('actor'))
                    new_configuration = 'preview_config' in body or 'deployment_profiles' in body
                    def register():
                        project = app.project_registration.add(body)
                        if body.get('make_default') is True: app.default_project = project['id']
                        return project
                    if path == '/api/v1/projects':
                        project = app.mutations.run(path, body.get('submission_key'), body, register) if new_configuration else register()
                        return self._json(201, project)
                    parts = path.split('/')
                    if len(parts) == 6 and parts[-1] == 'settings':
                        producer = lambda: app.project_registration.configure(parts[-2], body)
                        project = app.mutations.run(path, body.get('submission_key'), body, producer) if new_configuration else producer()
                        if body.get('make_default') is True: app.default_project = project['id']
                        return self._json(200, project)
                    raise ValueError('项目操作路径无效')
                if path == '/api/v1/initiatives' or path.startswith('/api/v1/initiatives/'):
                    actor = str(body.get('actor') or '').strip()
                    if not actor or len(actor) > 80 or actor.startswith('agent:'):
                        raise ValueError('事项与决定需要具名人操作')
                    if '/workflow/' in path:
                        if self.client_address[0] not in {'127.0.0.1', '::1'}:
                            raise ValueError('事项执行只允许从本机工作台操作')
                        parts = path.split('/')
                        if len(parts) != 7 or parts[-2] != 'workflow':
                            raise ValueError('事项操作路径无效')
                        item_id, action = parts[-3], parts[-1]
                        service, revision = app.initiative_workflow, body.get('revision')
                        if action in {'delivery-route', 'confirm-execute', 'rework', 'renew-authorization'}:
                            def decide_delivery():
                                if action == 'delivery-route':
                                    return service.configure_delivery(item_id, actor, revision, body.get('mode'), body.get('note'))
                                if action == 'confirm-execute':
                                    return service.confirm_execute(item_id, actor, revision, body.get('reviewer'),
                                        body.get('success_metric'), body.get('confirmed'))
                                if action == 'rework':
                                    return service.rework(item_id, actor, revision, body.get('reason'), body.get('note'))
                                return service.renew_authorization(item_id, actor, revision, body.get('note'), body.get('confirmed'))
                            status = 202 if action in {'confirm-execute', 'rework'} else 200
                            return self._json(status, app.mutations.run(path, body.get('submission_key'), body, decide_delivery))
                        if action == 'learning':
                            fields = body.get('fields')
                            if isinstance(fields, dict) and fields.get('action') in {'generate', 'cancel_generation'}:
                                return self._json(200, app.mutations.run(path, body.get('submission_key'), body,
                                    lambda: service.learning_action(item_id, actor, revision, fields)))
                            return self._json(200, service.learning_action(item_id, actor, revision, body.get('fields')))
                        if action == 'v0':
                            return self._json(200, service.submit_v0(item_id, actor, revision,
                                spec_text=body.get('spec_text'), execution_mode=body.get('execution_mode'),
                                workspace_path=body.get('workspace_path'), write_scope=body.get('write_scope'),
                                execution_timeout_seconds=body.get('execution_timeout_seconds'), confirmed=body.get('confirmed')))
                        if action in {'release', 'outcome'}:
                            return self._json(200, service.record_delivery(item_id, actor, revision, action, body.get('fields')))
                        if action == 'reopen':
                            return self._json(200, service.reopen(item_id, actor, revision, body.get('text')))
                        if action == 'discuss':
                            return self._json(202, service.discuss(item_id, actor, revision, body.get('text')))
                        if action == 'confirm-prd':
                            return self._json(200, service.confirm_prd(item_id, actor, revision, body.get('success_metric')))
                        if action == 'cancel':
                            return self._json(200, service.cancel(item_id, actor, revision,
                                run_id=body.get('run_id'), session_id=body.get('session_id'),
                                control_revision=body.get('control_revision')))
                        if action == 'confirm':
                            return self._json(200, service.confirm(item_id, actor, revision, body.get('reviewer')))
                        if action == 'execute':
                            return self._json(202, service.execute(item_id, actor, revision))
                        if action == 'eval':
                            return self._json(202, service.run_eval(item_id, actor, revision))
                        if action == 'loop-config':
                            return self._json(200, service.configure_loop(
                                item_id, actor, revision, body.get('fields')))
                        if action == 'ci-evidence':
                            return self._json(200, service.record_ci_evidence(
                                item_id, actor, revision, body.get('fields')))
                        if action == 'prepare-hook':
                            return self._json(200, service.prepare_hook(item_id, actor, revision))
                        if action == 'accept':
                            return self._json(200, service.accept(item_id, actor, revision, body.get('note')))
                        if action == 'integrate':
                            return self._json(202, service.integrate(item_id, actor, revision))
                        return self._json(404, {'error': 'not_found'})
                    if path == '/api/v1/initiatives':
                        data = body.get('data')
                        if not isinstance(data, dict):
                            raise ValueError('事项内容必须是对象')
                        data = dict(data)
                        data['project_id'] = data.get('project_id') or app.default_project
                        if data['project_id'] == 'FlowERP': data['project_id'] = app.default_project
                        app.projects.get(data['project_id'])
                        return self._json(201, app.initiatives.create(data, actor, submission_key=body.get('submission_key')))
                    parts = path.split('/')
                    if len(parts) == 6:
                        item_id, action = parts[-2:]
                        version = int(body.get('version', 0))
                        if action in {'clear-home', 'restore-home'}:
                            return self._json(200, app.initiatives.set_home_hidden(
                                item_id, action == 'clear-home', actor, version))
                        if action == 'revise':
                            original = app.initiatives.get(item_id)
                            if body.get('data', {}).get('project_id', original['project_id']) != original['project_id']:
                                raise ValueError('事项创建后不能切换项目')
                            return self._json(200, app.initiatives.revise(item_id, body.get('data', {}), actor, version))
                        if action == 'decide':
                            item = app.initiatives.get(item_id)
                            if item['decision']:
                                raise ValueError('已保存的决定不可覆盖，请创建复查事项')
                            return self._json(200, app.initiatives.decide(item_id, body.get('decision'), actor,
                                body.get('rationale'), version, review_trigger=body.get('review_trigger', '')))
                    return self._json(404, {'error': 'not_found'})
                if path.startswith('/api/v1/execution/'):
                    if self.client_address[0] not in {'127.0.0.1', '::1'}:
                        return self._json(403, {'error': 'cross_origin', 'message': '执行授权必须来自当前工作台页面'})
                    if path == '/api/v1/execution/daily-plans':
                        return self._json(201, app.code.prepare_daily(body.get('actor'), body.get('request'),
                            body.get('acceptance'), body.get('write_scope'), body.get('non_goals'),
                            initiative_id=body.get('initiative_id'), expected_revision=body.get('expected_revision')))
                    if path == '/api/v1/execution/plans':
                        return self._json(201, app.code.prepare(int(body.get('lesson', 0)), body.get('actor'),
                                                               body.get('eval_cases', []), body.get('session_ref'),
                                                               body.get('bootstrap_task_id'), body.get('requirement_spec_text'),
                                                               body.get('initiative_id'), body.get('initiative_version')))
                    if path.endswith('/authorize'):
                        return self._json(202, app.code.authorize(path.split('/')[-2], body.get('confirmation'), body.get('actor')))
                if path == "/api/v1/tasks":
                    return self._json(201, app.submit_and_verify(
                        int(body.get("lesson", 0)),
                        str(body.get("request", "")),
                        str(body.get("actor", "")),
                        body.get('project_id'),
                        body.get('verification_key'),
                    ))
                if path == "/api/v1/delivery/requests":
                    if body.get("execution_mode", "verify") != "verify":
                        raise ValueError("网页API只允许verify；代码执行须使用明确授权的课程CLI")
                    return self._json(202, app.accept_course_task(
                        int(body.get("lesson", 0)), str(body.get("request", "")),
                        str(body.get("actor", "")), self.headers.get("Idempotency-Key", ""),
                        body.get('business_refs'),
                        body.get('project_id'),
                        body.get('verification_key'),
                    ))
                parts = [item for item in path.split("/") if item]
                if parts[:3] == ["api", "v1", "tasks"] and len(parts) == 6 and parts[4] == 'graph':
                    task_id, action = parts[3], parts[5]
                    key = self.headers.get('Idempotency-Key', '')
                    common = {"actor": str(body.get("actor", "")),
                              "expected_version": int(body.get("expected_version", 0)), "key": key}
                    if action == 'advance':
                        return self._json(200, app.graphs.advance(task_id, **common))
                    if action == 'authorize':
                        return self._json(200, app.graphs.authorize(
                            task_id, candidate_sha256=str(body.get('candidate_sha256', '')), **common))
                    if action == 'decisions':
                        return self._json(200, app.graphs.decide(
                            task_id, decision_type=str(body.get('decision_type', '')),
                            decision=str(body.get('decision', '')), role=str(body.get('role', '')),
                            reason=str(body.get('reason', '')),
                            candidate_revision=int(body.get('candidate_revision', 0)),
                            candidate_sha256=str(body.get('candidate_sha256', '')),
                            target_ref=str(body.get('target_ref', '')), **common))
                    if action == 'recover':
                        return self._json(200, app.graphs.recover(
                            task_id, reason=str(body.get('reason', '')), **common))
                    if action == 'reconcile':
                        return self._json(200, app.graphs.reconcile(
                            task_id, evidence=body.get('evidence') if isinstance(body.get('evidence'), dict) else {},
                            **common))
                if parts[:3] == ["api", "v1", "tasks"] and len(parts) == 5:
                    task_id, action = parts[3], parts[4]
                    if action == 'preview':
                        if self.client_address[0] not in {'127.0.0.1', '::1'}:
                            raise ValueError('候选预览只允许从本机工作台打开')
                        try:
                            if body.get('plan_id'):
                                return self._json(200, app.mutations.run(path, body.get('submission_key'), body,
                                    lambda: app.previews.start(task_id, body.get('actor'), body)))
                            return self._json(200, app.previews.start(task_id, body.get('actor'), body))
                        except (OSError, RuntimeError) as error:
                            return self._json(503, {'error':'preview_unavailable', 'message':str(error)})
                    if action == "verify":
                        return self._json(200, app.verify_task(task_id, str(body.get("actor", ""))))
                    if action == "review":
                        return self._json(200, app.review_task(
                            task_id,
                            str(body.get("reviewer", "")),
                            str(body.get("decision", "")),
                            str(body.get("note", "")),
                        ))
                return self._json(404, {"error": "not_found"})
            except CourseVerificationNotReady as exc:
                self._json(409, {'error': 'course_verification_not_ready', 'message': str(exc),
                                 'verification': exc.verification, 'automatic_replay': False})
            except MutationPending as exc:
                self._json(409, {'error': 'mutation_pending', 'message': str(exc), 'automatic_replay': False})
            except (TaskSubmissionConflict, WorkflowConflict, InitiativeSubmissionConflict) as exc:
                self._json(409, {"error": "conflict", "message": str(exc)})
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid_json", "message": "请求体不是合法 JSON"})
            except KeyError as exc:
                self._json(404, {"error": "not_found", "message": str(exc)})
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": type(exc).__name__, "message": str(exc)})

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8001, runtime_dir: str = ".runtime", *, enable_code_execution=False,
          erp_url='http://127.0.0.1:8000', enable_advanced_runtime=False) -> None:
    if enable_code_execution and host not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError("网页代码执行只允许绑定本机回环地址")
    with WorkbenchRuntimeLease(runtime_dir):
        app = WorkbenchApp(runtime_dir, port=port, enable_code_execution=enable_code_execution, erp_url=erp_url,
                           enable_advanced_runtime=enable_advanced_runtime)
        server = create_http_server(
            host, port, make_handler(app), service_name="个人研发工作台",
            retry_command="python -X utf8 -m workbench.cli serve-workbench --port 8081",
            server_class=WorkbenchHTTPServer,
        )
        try:
            app.tasks.quarantine_interrupted_web_code_tasks()
            app.delivery_runtime.recover()
            if not (app.runtime / RESTORE_MARKER).is_file():
                app.automation.recover()
            print(f"个人研发工作台 http://{host}:{port}  （客户项目 FlowERP 在 {app.erp_url}）", flush=True)
            server.serve_forever()
        finally:
            primary_error = sys.exc_info()[0] is not None
            cleanup_error = None
            closers = [app.initiative_workflow.learning_generation.close, app.delivery_runtime.close,
                       app.deployments.close]
            if app.harness_api:
                closers.append(app.harness_api.shutdown)
            closers.extend([app.previews.close, server.server_close])
            for close in closers:
                try:
                    close()
                except Exception as error:
                    # A broken store must not leave another owned process or
                    # the listening socket alive by skipping later cleanup.
                    logging.getLogger('workbench.shutdown').exception('受管资源关闭失败，继续关闭其余资源')
                    if cleanup_error is None:
                        cleanup_error = error
            if cleanup_error is not None and not primary_error:
                raise cleanup_error
