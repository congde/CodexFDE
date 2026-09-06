from __future__ import annotations

import json
import mimetypes
import uuid
import sys
from .project_store import ProjectStore
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .cockpit import current_course, last_upgrade, lesson_eval_runner, lesson_number_from_requirement
from .course_mainline import lesson_contract, validate_mainline, write_lesson_spec
from .delivery_view import DeliveryViewService
from .evolution import EvolutionStore
from .http_bind import create_http_server
from .task_store import TaskStore, TaskSubmissionConflict
from .automation import DeliveryAutomation
from .workflow import run_task
from .web_execution import WebExecution
from .runtime_lease import WorkbenchRuntimeLease
from .initiative import InitiativeStore
from .candidate_preview import CandidatePreviews
from .initiative_workflow import InitiativeWorkflow


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = (ROOT / "workbench_web").resolve()


class WorkbenchApp:
    """Required follow-along cockpit. Owns workbench.db only."""

    def __init__(self, runtime_dir: str | Path = ".runtime", *, port: int = 8001, eval_factory=None,
                 enable_code_execution=False, erp_url='http://127.0.0.1:8000') -> None:
        target = urlparse(erp_url)
        if (target.scheme != 'http' or target.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or target.username or target.password or target.query or target.fragment
                or target.path not in {'', '/'} or (target.port or 80) == port):
            raise ValueError('客户项目地址必须是独立的本机 HTTP 服务地址')
        self.erp_url = erp_url.rstrip('/')
        self.runtime = Path(runtime_dir).resolve()
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.port = port
        self.eval_factory = eval_factory
        self.tasks = TaskStore(self.runtime / "workbench.db")
        self.projects = ProjectStore(self.tasks.path)
        existing = next((p for p in self.projects.list() if Path(p['root_path']) == ROOT), None)
        default = existing or self.projects.create('FlowERP', ROOT,
            [str(ROOT / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')),
             '-X', 'utf8', '-m', 'eval.harness', '--suite', 'blocking', '--report-path', '{report_path}'],
            project_id='PROJECT-FLOWERP')
        self.initiatives = InitiativeStore(self.tasks.path)
        with self.initiatives.connect() as db:
            db.execute("UPDATE initiatives SET project_id=? WHERE project_id IN ('', 'FlowERP')", (default['id'],))
        self.default_project = default['id']
        self.evolutions = EvolutionStore(self.tasks.path)
        self.views = DeliveryViewService(self.tasks, self.evolutions)
        self.code = WebExecution(ROOT, self.runtime, self.tasks, enabled=enable_code_execution)
        self.previews = CandidatePreviews(self.runtime, self.tasks)
        self.initiative_workflow = InitiativeWorkflow(ROOT, self.runtime, self.initiatives, self.tasks,
                                                     enabled=enable_code_execution, projects=self.projects)
        self.automation = DeliveryAutomation(
            self.tasks, self.runtime, max_attempts=1,
            agent_runner=lambda task_id, actor: self._run_course_task(task_id, actor),
        )

    def _run_course_task(self, task_id: str, actor: str) -> dict:
        self.verify_task(task_id, actor)
        return self.tasks.get(task_id)

    def health(self) -> dict:
        return {
            "status": "ok",
            "surface": "workbench",
            "workbench_port": self.port,
            "erp_url": self.erp_url,
            "runtime": str(self.runtime),
            "database": str(self.runtime / "workbench.db"),
            "erp_database": None,
            "message": f"这是个人研发工作台。FlowERP 是客户项目案例，请在 {self.erp_url} 打开。",
            "thesis": {
                "workbench": "研发工作台是写代码的主体",
                "flowerp": "FlowERP 是验证场，证明工作台有效且能自迭代",
                "codex": "Codex 是底座，既造工作台也被工作台约束",
            },
        }

    def course_current(self, lesson: int | None = None) -> dict:
        return current_course(lesson, tasks=self.tasks.list(50))

    def upgrade(self) -> dict:
        return last_upgrade(self.evolutions)

    def create_course_task(self, lesson: int, request: str, actor: str, *, submission_key: str | None = None, business_refs=None) -> dict:
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
        spec_path = self.runtime / "course" / f"L{int(lesson):02d}" / uuid.uuid4().hex / "FDE_SPEC.md"
        write_lesson_spec(int(lesson), spec_path)
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
        )
        if task["spec_path"] != str(spec_path):
            # This attempt owns only the unused newly-generated file.
            spec_path.unlink()
            spec_path.parent.rmdir()
            return self.views.get(task["id"])
        self.tasks.append_event(
            task["id"],
            "Web 已创建受控课程任务；尚未授权 Codex 写入",
            actor=actor,
            evidence={"lesson": int(lesson), "execution_mode": "verify"},
        )
        return self.views.get(task["id"])

    def accept_course_task(self, lesson: int, request: str, actor: str, key: str, business_refs=None) -> dict:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("异步提交必须提供 Idempotency-Key")
        created = self.create_course_task(lesson, request, actor, submission_key=key.strip(), business_refs=business_refs)
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
        if task.get('execution_mode') == 'codex':
            raise ValueError("代码任务须通过课程执行方案处理，不能从仅复验入口运行")
        status = str(task.get("status") or "")
        if status not in {"queued", "spec_ready", "rework"}:
            raise ValueError(f"当前状态 {status} 不能再跑复验")
        lesson = lesson_number_from_requirement(str(task.get("requirement_id") or ""))
        run_task(self.tasks, task_id, actor, suite_runner=(self.eval_factory or lesson_eval_runner)(lesson))
        return self.views.get(task_id)

    def submit_and_verify(self, lesson: int, request: str, actor: str) -> dict:
        created = self.create_course_task(lesson, request, actor)
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
        if task.get('execution_mode') == 'codex' and decision == 'approve' and not daily:
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
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if path == "/api/health":
                    return self._json(200, app.health())
                if path == '/api/v1/projects':
                    return self._json(200, {'items': app.projects.list(), 'default_project': app.default_project})
                if path == '/api/v1/initiatives':
                    return self._json(200, {'items': app.initiatives.list(100)})
                if path.startswith('/api/v1/initiatives/'):
                    if path.endswith('/workflow'):
                        return self._json(200, app.initiative_workflow.get(path.split('/')[-2]))
                    return self._json(200, app.initiatives.get(path.rsplit('/', 1)[-1]))
                if path == "/api/v1/delivery/capabilities":
                    return self._json(200, {"surface": "workbench", "async_submission": True,
                                            "execution_modes": ["verify"], "requires_idempotency_key": True,
                                            "code_execution": "reviewable_plan" if app.code.enabled else "explicit_course_cli_only",
                                            "web_code_execution": app.code.enabled,
                                            "code_readiness": app.code.readiness()})
                if path.startswith('/api/v1/execution/plans/'):
                    return self._json(200, app.code.get(path.rsplit('/', 1)[-1]))
                if path == "/api/course/current":
                    raw = (query.get("lesson") or [""])[0]
                    lesson = int(raw) if str(raw).isdigit() else None
                    return self._json(200, app.course_current(lesson))
                if path == "/api/cockpit/upgrade":
                    return self._json(200, app.upgrade())
                if path == "/api/course/status":
                    return self._json(200, validate_mainline(ROOT))
                if path in {"/api/tasks", "/api/v1/tasks"}:
                    limit = int((query.get("limit") or ["30"])[0])
                    return self._json(200, {"items": app.tasks.list(limit)})
                if path.startswith("/api/v1/tasks/"):
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
            try:
                path = urlparse(self.path).path
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    return self._json(415, {"error": "unsupported_media_type", "message": "只接受 application/json"})
                length = int(self.headers.get("Content-Length", "0"))
                maximum = 131072 if path in {'/api/v1/execution/plans', '/api/v1/execution/daily-plans'} else 32768 if path.startswith('/api/v1/initiatives') else 8192
                if length <= 0 or length > maximum:
                    return self._json(400, {"error": "invalid_body", "message": "请求体为空或过大"})
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("请求体必须是 JSON 对象")
                if path == '/api/v1/projects':
                    if self.client_address[0] not in {'127.0.0.1', '::1'} or (self.headers.get('Origin') and self.headers['Origin'] != 'http://' + self.headers.get('Host', '')):
                        raise ValueError('项目登记只允许从本机工作台操作')
                    app.initiative_workflow.actor(body.get('actor'))
                    command = body.get('eval_command')
                    if not isinstance(command, list) or not command or any(not isinstance(p, str) or not p.strip() for p in command):
                        raise ValueError('Eval 命令必须是非空参数数组')
                    root = body.get('root_path')
                    name = body.get('name')
                    if not isinstance(root, str) or not root.strip() or not Path(root).is_absolute() or not isinstance(name, str) or not name.strip():
                        raise ValueError('请填写项目名称与源码目录的绝对路径')
                    if not Path(command[0]).is_absolute() or not Path(command[0]).is_file():
                        raise ValueError('测试命令首项必须是项目运行环境中可执行程序的绝对路径')
                    if any(Path(p['root_path']) == Path(root).resolve() for p in app.projects.list()):
                        raise ValueError('该源码目录已登记，请选择已有项目')
                    return self._json(201, app.projects.create(name, root, command))
                if path == '/api/v1/initiatives' or path.startswith('/api/v1/initiatives/'):
                    actor = str(body.get('actor') or '').strip()
                    if not actor or len(actor) > 80 or actor.startswith('agent:'):
                        raise ValueError('事项与决定需要具名人操作')
                    if '/workflow/' in path:
                        if self.client_address[0] not in {'127.0.0.1', '::1'}:
                            raise ValueError('事项执行只允许从本机工作台操作')
                        origin = self.headers.get('Origin')
                        if origin and origin != 'http://' + self.headers.get('Host', ''):
                            return self._json(403, {'error': 'cross_origin', 'message': '请从当前工作台页面操作'})
                        parts = path.split('/')
                        if len(parts) != 7 or parts[-2] != 'workflow':
                            raise ValueError('事项操作路径无效')
                        item_id, action = parts[-3], parts[-1]
                        service, revision = app.initiative_workflow, body.get('revision')
                        if action in {'release', 'outcome'}:
                            return self._json(200, service.record_delivery(item_id, actor, revision, action, body.get('fields')))
                        if action == 'reopen':
                            return self._json(200, service.reopen(item_id, actor, revision, body.get('text')))
                        if action == 'discuss':
                            return self._json(202, service.discuss(item_id, actor, revision, body.get('text')))
                        if action == 'confirm-prd':
                            return self._json(200, service.confirm_prd(item_id, actor, revision, body.get('success_metric')))
                        if action == 'cancel':
                            return self._json(200, service.cancel(item_id, actor, revision))
                        if action == 'confirm':
                            return self._json(200, service.confirm(item_id, actor, revision, body.get('reviewer')))
                        if action == 'execute':
                            return self._json(202, service.execute(item_id, actor, revision))
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
                        return self._json(201, app.initiatives.create(data, actor))
                    parts = path.split('/')
                    if len(parts) == 6:
                        item_id, action = parts[-2:]
                        version = int(body.get('version', 0))
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
                    origin = self.headers.get('Origin')
                    if origin and origin != 'http://' + self.headers.get('Host', ''):
                        return self._json(403, {'error': 'cross_origin', 'message': '执行授权必须来自当前工作台页面'})
                    if path == '/api/v1/execution/daily-plans':
                        return self._json(201, app.code.prepare_daily(body.get('actor'), body.get('request'),
                            body.get('acceptance'), body.get('write_scope'), body.get('non_goals', '不扩大本次需求范围')))
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
                    ))
                if path == "/api/v1/delivery/requests":
                    if body.get("execution_mode", "verify") != "verify":
                        raise ValueError("网页API只允许verify；代码执行须使用明确授权的课程CLI")
                    return self._json(202, app.accept_course_task(
                        int(body.get("lesson", 0)), str(body.get("request", "")),
                        str(body.get("actor", "")), self.headers.get("Idempotency-Key", ""),
                        body.get('business_refs'),
                    ))
                parts = [item for item in path.split("/") if item]
                if parts[:3] == ["api", "v1", "tasks"] and len(parts) == 5:
                    task_id, action = parts[3], parts[4]
                    if action == 'preview':
                        if self.client_address[0] not in {'127.0.0.1', '::1'}:
                            raise ValueError('候选预览只允许从本机工作台打开')
                        origin = self.headers.get('Origin')
                        if origin and origin != 'http://' + self.headers.get('Host', ''):
                            raise ValueError('请从当前工作台页面打开候选成果')
                        try:
                            return self._json(200, app.previews.start(task_id, body.get('actor')))
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
            except TaskSubmissionConflict as exc:
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
          erp_url='http://127.0.0.1:8000') -> None:
    if enable_code_execution and host not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError("网页代码执行只允许绑定本机回环地址")
    with WorkbenchRuntimeLease(runtime_dir):
        app = WorkbenchApp(runtime_dir, port=port, enable_code_execution=enable_code_execution, erp_url=erp_url)
        server = create_http_server(
            host, port, make_handler(app), service_name="个人研发工作台",
            retry_command="python -X utf8 -m workbench.cli serve-workbench --port 8081",
        )
        try:
            app.tasks.quarantine_interrupted_web_code_tasks()
            app.automation.recover()
            print(f"个人研发工作台 http://{host}:{port}  （客户项目 FlowERP 在 {app.erp_url}）", flush=True)
            server.serve_forever()
        finally:
            app.previews.close()
            server.server_close()
