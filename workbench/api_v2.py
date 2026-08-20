from __future__ import annotations

import json
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from http import HTTPStatus
from urllib.parse import parse_qs
from pathlib import Path

from flowerp.audit import AuditService
from flowerp.config import Settings
from flowerp.finance import FinanceService
from flowerp.identity import IdentityService, Principal, SYSTEM_PRINCIPAL
from flowerp.idempotency import IdempotencyService
from flowerp.inventory import InventoryService
from flowerp.master_data import MasterDataService
from flowerp.models import AuthenticationError, Conflict, DomainError, NotFound, PermissionDenied, ValidationError
from flowerp.operations import HealthService, RuntimeCoordinator
from flowerp.purchasing import PurchasingService
from flowerp.reports import ReportService
from flowerp.sales import SalesService
from flowerp.store import ERPStore
from flowerp.pricing import PricingService
from flowerp.serials import SerialNumberService
from flowerp.import_export import ImportExportService
from flowerp.reconciliation import ReconciliationService
from flowerp.partners import PartnerDetailService
from flowerp.alerts import AlertService
from flowerp.accounting import LedgerService
from flowerp.cash_management import CashManagementService
from flowerp.channels import EcommerceChannelService
from .feedback import add_feedback, review_feedback, summary as feedback_summary
from .evolution import EvolutionStore
from .automation import DeliveryAutomation
from .execution import CodexExecutionRunner
from .task_store import TaskStore
from .workflow import evaluate_task, prepare_task, run_task, start_task


@dataclass
class APIResponse:
    status: int
    body: object
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "application/json; charset=utf-8"


class APIRouter:
    """Dependency-light JSON API router for single-instance FlowERP deployments."""

    def __init__(self, store: ERPStore, settings: Settings, task_store: TaskStore | None = None,
                 automation: DeliveryAutomation | None = None) -> None:
        self.store = store
        self.settings = settings
        self.identity = IdentityService(store, settings.session_hours)
        self.identity.ensure_local_defaults()
        self.audit = AuditService(store)
        self.master = MasterDataService(store, self.audit)
        self.inventory = InventoryService(store, self.audit)
        self.sales = SalesService(store, self.audit)
        self.purchasing = PurchasingService(store, self.audit)
        self.finance = FinanceService(store, self.audit)
        self.reports = ReportService(store)
        self.health = HealthService(
            store, settings.runtime_dir, settings.minimum_free_disk_mb,
            settings.backup_max_age_hours, settings.require_recent_backup,
        )
        self.runtime = RuntimeCoordinator(store)
        self.idempotency = IdempotencyService(store)
        self.pricing = PricingService(store, self.audit)
        self.serials = SerialNumberService(store, self.audit)
        self.imports = ImportExportService(store, self.master, self.audit)
        self.reconciliation = ReconciliationService(store, self.audit)
        self.partner_details = PartnerDetailService(store, self.audit)
        self.alerts = AlertService(store, self.audit)
        self.ledger = LedgerService(store)
        self.cash = CashManagementService(store, self.audit)
        self.channels = EcommerceChannelService(store, self.audit)
        self.tasks = task_store or TaskStore(settings.runtime_dir / "workbench.db")
        self.evolutions = EvolutionStore(self.tasks.path)
        self.automation = automation or DeliveryAutomation(
            self.tasks, settings.runtime_dir,
            execution_runner=CodexExecutionRunner(Path(__file__).resolve().parent.parent, settings.runtime_dir),
        )
        self.automation.recover()
        self._login_attempts: dict[str, deque[float]] = defaultdict(deque)
        self._request_attempts: dict[str, deque[float]] = defaultdict(deque)
        self._rate_lock = threading.Lock()
        self._capacity = threading.BoundedSemaphore(settings.max_concurrent_requests)
        self._metric_lock = threading.Lock()
        self._requests_total = 0
        self._responses_5xx_total = 0
        self._rate_rejections_total = 0
        self._overload_rejections_total = 0
        self._request_duration_seconds = 0.0

    def dispatch(self, method: str, raw_path: str, headers: dict[str, str], body: object,
                 remote_addr: str = "") -> APIResponse:
        started = time.monotonic()
        request_id = headers.get("x-request-id", "").strip()[:128] or f"REQ-{uuid.uuid4().hex.upper()}"
        path, _, query_string = raw_path.partition("?")
        query = {key: values[-1] for key, values in parse_qs(query_string).items()}
        normalized_path = path.rstrip("/") or "/"
        exempt = normalized_path.startswith("/api/v1/health/") or normalized_path == "/api/v1/metrics"
        if not exempt and not self._check_request_rate(remote_addr):
            response = self._error(HTTPStatus.TOO_MANY_REQUESTS, "rate_limited", "请求过于频繁，请稍后重试")
            response.headers["Retry-After"] = "60"
            with self._metric_lock: self._rate_rejections_total += 1
        elif not self._capacity.acquire(blocking=False):
            response = self._error(HTTPStatus.SERVICE_UNAVAILABLE, "overloaded", "服务繁忙，已主动拒绝请求以保护业务事务")
            response.headers["Retry-After"] = "2"
            with self._metric_lock: self._overload_rejections_total += 1
        else:
            try:
                try:
                    response = self._dispatch(method.upper(), normalized_path, query, headers, body, remote_addr)
                except AuthenticationError as exc:
                    response = self._error(HTTPStatus.UNAUTHORIZED, "authentication_failed", str(exc))
                except PermissionDenied as exc:
                    response = self._error(HTTPStatus.FORBIDDEN, "permission_denied", str(exc))
                except NotFound as exc:
                    response = self._error(HTTPStatus.NOT_FOUND, "not_found", str(exc))
                except Conflict as exc:
                    response = self._error(HTTPStatus.CONFLICT, "conflict", str(exc))
                except (ValidationError, ValueError, KeyError, TypeError) as exc:
                    response = self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "validation_error", str(exc))
                except DomainError as exc:
                    response = self._error(HTTPStatus.CONFLICT, type(exc).__name__, str(exc))
                except Exception as exc:
                    message = str(exc) if self.settings.debug else "服务器内部错误"
                    response = self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", message)
            finally:
                self._capacity.release()
        response.headers.setdefault("X-Request-ID", request_id)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Cache-Control", "no-store")
        origin = headers.get("origin", "").rstrip("/")
        if origin and origin in self.settings.allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        with self._metric_lock:
            self._requests_total += 1
            self._request_duration_seconds += time.monotonic() - started
            if response.status >= 500: self._responses_5xx_total += 1
        return response

    @staticmethod
    def _error(status: int, code: str, message: str, details: object = None) -> APIResponse:
        body: dict[str, object] = {"error": {"code": code, "message": message}}
        if details is not None: body["error"]["details"] = details  # type: ignore[index]
        return APIResponse(int(status), body)

    def _dispatch(self, method: str, path: str, query: dict[str, str], headers: dict[str, str],
                  body: object, remote_addr: str) -> APIResponse:
        data = body if isinstance(body, dict) else {}
        if method == "OPTIONS":
            return APIResponse(204, "", {"Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
                                          "Access-Control-Allow-Headers": "Authorization,Content-Type,X-CSRF-Token,Idempotency-Key,X-Request-ID"})
        if path == "/api/v1/health/live" and method == "GET": return APIResponse(200, self.health.live())
        if path == "/api/v1/health/ready" and method == "GET":
            ok, result = self.health.ready(); return APIResponse(200 if ok else 503, result)
        if path == "/api/v1/metrics" and method == "GET":
            return APIResponse(200, self.health.metrics() + self._runtime_metrics(), content_type="text/plain; version=0.0.4; charset=utf-8")
        if path == "/api/v1/setup/status" and method == "GET":
            count = int(self.store.scalar("SELECT COUNT(*) FROM users") or 0)
            return APIResponse(200, {"initialized": count > 0, "authentication_required": self.settings.auth_required})
        if path == "/api/v1/setup/bootstrap" and method == "POST":
            result = self.identity.bootstrap(str(data.get("organization_name", "")), str(data.get("username", "")), str(data.get("password", "")))
            return APIResponse(201, {"user": result})
        if path == "/api/v1/auth/login" and method == "POST":
            self._check_login_rate(remote_addr)
            token, principal = self.identity.login(str(data.get("organization", "DEFAULT")), str(data.get("username", "")),
                                                   str(data.get("password", "")), remote_addr, headers.get("user-agent", ""))
            csrf = self.identity.csrf_token(principal)
            cookie = f"flowerp_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={self.settings.session_hours * 3600}"
            if self.settings.cookie_secure: cookie += "; Secure"
            return APIResponse(200, {"token": token, "csrf_token": csrf, "user": self._principal_json(principal)}, {"Set-Cookie": cookie})

        principal, cookie_auth = self._principal(headers)
        if method in {"POST", "PUT", "PATCH", "DELETE"} and cookie_auth:
            self.identity.validate_csrf(principal, headers.get("x-csrf-token", ""))

        if path == "/api/v1/operations/status" and method == "GET":
            principal.require("users.manage")
            return APIResponse(200, self.runtime.status())
        if path == "/api/v1/operations/maintenance" and method == "POST":
            principal.require("users.manage")
            enabled = data.get("enabled", True)
            if not isinstance(enabled, bool): raise ValidationError("enabled 必须为布尔值")
            return APIResponse(200, self.runtime.set_maintenance(
                enabled, str(data.get("reason", "")), principal.user_id
            ))
        if method in {"POST", "PUT", "PATCH", "DELETE"} and self.runtime.status().get("maintenance_mode"):
            return APIResponse(503, {"error": {"code": "maintenance_mode", "message": "系统处于维护模式，暂时停止业务写入"}}, {"Retry-After": "60"})

        if path == "/api/v1/auth/me" and method == "GET": return APIResponse(200, self._principal_json(principal))
        if path == "/api/v1/auth/logout" and method == "POST":
            if principal.session_id: self.identity.logout(principal)
            return APIResponse(204, "", {"Set-Cookie": "flowerp_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
        if path == "/api/v1/auth/password" and method == "POST":
            self.identity.change_password(principal, str(data.get("old_password", "")), str(data.get("new_password", "")))
            return APIResponse(204, "")

        if path == "/api/v1/delivery/capabilities":
            principal.require("users.manage")
            if method == "GET":
                return APIResponse(200, self.automation.capabilities())
        if path == "/api/v1/delivery/requests":
            principal.require("users.manage")
            if method == "POST":
                refs = data.get("business_refs", [])
                if not isinstance(refs, list):
                    raise ValidationError("business_refs 必须为数组")
                scopes = data.get("write_scope", [])
                if not isinstance(scopes, list):
                    raise ValidationError("write_scope 必须为数组")
                return self._idempotent(principal, method, path, headers, data, lambda: (
                    202, self.automation.submit(
                        str(data.get("request", "")), str(data.get("requirement_id", "")),
                        [str(value) for value in refs], principal.username,
                        str(data.get("execution_mode", "verify")), [str(value) for value in scopes],
                        int(data.get("execution_timeout_seconds", 900)),
                    )
                ))
        if path == "/api/v1/tasks":
            principal.require("users.manage")
            if method == "GET":
                return APIResponse(200, {"items": self.tasks.list(self._integer(query, "limit", 30))})
            if method == "POST":
                refs = data.get("business_refs", [])
                if not isinstance(refs, list):
                    raise ValidationError("business_refs 必须为数组")
                return self._idempotent(principal, method, path, headers, data, lambda: (
                    201, self.tasks.create(
                        str(data.get("request", "")), str(data.get("requirement_id", "")),
                        [str(value) for value in refs], str(data.get("spec_path", "FDE_SPEC.md")),
                        principal.username,
                    )
                ))
        if path.startswith("/api/v1/tasks/"):
            principal.require("users.manage")
            parts = path.split("/"); task_id = parts[4]
            try:
                if len(parts) == 5 and method == "GET":
                    return APIResponse(200, self.tasks.get(task_id))
                if len(parts) == 6 and parts[5] == "run" and method == "POST":
                    return self._idempotent(
                        principal, method, path, headers, data,
                        lambda: self._run_delivery_task_response(task_id, principal.username),
                    )
                if len(parts) == 6 and parts[5] == "prepare" and method == "POST":
                    return self._idempotent(principal, method, path, headers, data, lambda: (
                        200, prepare_task(self.tasks, task_id, principal.username)
                    ))
                if len(parts) == 6 and parts[5] == "start" and method == "POST":
                    return self._idempotent(principal, method, path, headers, data, lambda: (
                        200, start_task(self.tasks, task_id, principal.username)
                    ))
                if len(parts) == 6 and parts[5] == "evaluate" and method == "POST":
                    return self._idempotent(principal, method, path, headers, data, lambda: (
                        200, evaluate_task(self.tasks, task_id, principal.username)
                    ))
                if len(parts) == 6 and parts[5] == "review" and method == "POST":
                    return self._idempotent(principal, method, path, headers, data, lambda: (
                        200, self.tasks.review(
                            task_id, principal.username, str(data.get("decision", "")), str(data.get("note", "")),
                        )
                    ))
            except KeyError as exc:
                raise NotFound(f"任务不存在：{task_id}") from exc

        if path == "/api/v1/feedback":
            principal.require("users.manage")
            if method == "GET":
                return APIResponse(200, feedback_summary(self.tasks.path))
            if method == "POST":
                return self._idempotent(principal, method, path, headers, data, lambda: (201, add_feedback(
                    str(data.get("task_id", "")), str(data.get("source", "")),
                    str(data.get("conclusion", "")), str(data.get("next_step", "")), self.tasks.path,
                )))
        if path.startswith("/api/v1/feedback/"):
            principal.require("users.manage")
            parts = path.split("/"); feedback_id = parts[4]
            if len(parts) == 6 and parts[5] == "review" and method == "POST":
                try:
                    return self._idempotent(principal, method, path, headers, data, lambda: (200, review_feedback(
                        feedback_id, principal.username, str(data.get("decision", "")),
                        str(data.get("note", "")), self.tasks.path,
                    )))
                except KeyError as exc:
                    raise NotFound(f"反馈不存在：{feedback_id}") from exc

        if path == "/api/v1/evolutions":
            principal.require("users.manage")
            if method == "GET":
                return APIResponse(200, self.evolutions.summary(self._integer(query, "limit", 100)))
            if method == "POST":
                refs = data.get("business_refs", [])
                if not isinstance(refs, list):
                    raise ValidationError("business_refs 必须为数组")
                try:
                    return self._idempotent(principal, method, path, headers, data, lambda: (
                        201, self.evolutions.create(
                            str(data.get("feedback_id", "")), str(data.get("failure_signature", "")),
                            str(data.get("classification", "")), [str(value) for value in refs] or None,
                            principal.username,
                        )
                    ))
                except KeyError as exc:
                    raise NotFound(f"反馈不存在：{data.get('feedback_id', '')}") from exc
        if path.startswith("/api/v1/evolutions/"):
            principal.require("users.manage")
            parts = path.split("/"); evolution_id = parts[4]
            try:
                if len(parts) == 5 and method == "GET":
                    return APIResponse(200, self.evolutions.get(evolution_id))
                if len(parts) == 6 and parts[5] == "review" and method == "POST":
                    return self._idempotent(principal, method, path, headers, data, lambda: (
                        200, self.evolutions.review(
                            evolution_id, principal.username, str(data.get("decision", "")),
                            str(data.get("note", "")),
                        )
                    ))
                if len(parts) == 6 and parts[5] == "assets" and method == "POST":
                    assets = data.get("asset_changes", [])
                    if not isinstance(assets, list):
                        raise ValidationError("asset_changes 必须为数组")
                    return self._idempotent(principal, method, path, headers, data, lambda: (
                        200, self.evolutions.record_assets(evolution_id, assets, principal.username)
                    ))
                if len(parts) == 6 and parts[5] == "verify" and method == "POST":
                    return self._idempotent(principal, method, path, headers, data, lambda: (
                        200, self.evolutions.verify(
                            evolution_id, str(data.get("candidate_task_id", "")),
                            str(data.get("blocking_report", "")), principal.username,
                        )
                    ))
            except KeyError as exc:
                raise NotFound(f"进化记录不存在：{evolution_id}") from exc

        if path == "/api/v1/users" and method == "GET": return APIResponse(200, {"items": self.identity.list_users(principal)})
        if path == "/api/v1/users" and method == "POST":
            user = self.identity.create_user(principal, str(data["username"]), str(data["display_name"]), str(data["password"]),
                                             [str(v) for v in data.get("roles", [])], str(data.get("email", "")))
            return APIResponse(201, user)
        if path.startswith("/api/v1/users/"):
            user_id = path.split("/")[4]
            if method == "GET": return APIResponse(200, self.identity.managed_user(principal, user_id))
            if method == "PATCH":
                return APIResponse(200, self.identity.update_user(
                    principal, user_id, int(data["version"]), data.get("display_name"), data.get("email"),
                    data.get("roles"), data.get("status"),
                ))

        if path == "/api/v1/dashboard" and method == "GET": return APIResponse(200, self.reports.dashboard(principal))
        if path == "/api/v1/dashboard/trends" and method == "GET":
            return APIResponse(200, self.reports.dashboard_trends(
                principal, self._integer(query, "months", 12), query.get("as_of") or None
            ))

        if path == "/api/v1/sites" and method == "GET": return APIResponse(200, {"items": self.master.list_sites(principal, query.get("active", "true") != "false")})
        if path == "/api/v1/sites" and method == "POST":
            return APIResponse(201, self.master.create_site(principal, str(data["code"]), str(data["name"]), str(data.get("site_type", "warehouse")),
                                                            str(data.get("address", "")), str(data.get("contact_name", "")), str(data.get("contact_phone", ""))))
        if path.startswith("/api/v1/sites/"):
            parts = path.split("/"); site_id = parts[4]
            if len(parts) == 5 and method == "GET": return APIResponse(200, self.master.site(principal, site_id))
            if len(parts) == 6 and parts[5] == "locations" and method == "POST":
                return APIResponse(201, self.master.create_location(principal, site_id, str(data["code"]), str(data["name"]), str(data.get("location_type", "internal"))))

        if path == "/api/v1/categories" and method == "POST":
            return APIResponse(201, self.master.create_category(principal, str(data["code"]), str(data["name"]), str(data.get("parent_id") or "") or None))
        if path == "/api/v1/products" and method == "GET":
            return APIResponse(200, {"items": self.master.list_products(principal, query.get("q", ""), query.get("active", "true") != "false",
                                                                             self._integer(query, "limit", 100), self._integer(query, "offset", 0))})
        if path == "/api/v1/products" and method == "POST":
            return self._idempotent(principal, method, path, headers, data, lambda: (
                201, self.master.create_product(
                    principal, str(data["sku"]), str(data["name"]), int(data.get("sales_price_cents", 0)),
                    int(data.get("standard_cost_cents", 0)), str(data.get("barcode", "")),
                    str(data.get("category_id") or "") or None, str(data.get("tracking", "none")),
                    int(data.get("tax_rate_basis_points", 1300)), int(data.get("min_stock", 0)),
                    int(data.get("max_stock", 0)), str(data.get("description", "")), int(data.get("shelf_life_days", 0))
                )
            ))
        if path.startswith("/api/v1/products/"):
            product_id = path.split("/")[4]
            if method == "GET": return APIResponse(200, self.master.product(principal, product_id))
            if method == "PATCH":
                changes = {key: value for key, value in data.items() if key != "version"}
                return APIResponse(200, self.master.update_product(principal, product_id, int(data["version"]), **changes))

        if path == "/api/v1/customers" and method == "GET": return APIResponse(200, {"items": self.master.list_partners(principal, "customer", query.get("q", ""))})
        if path == "/api/v1/customers" and method == "POST":
            fields = {key: value for key, value in data.items() if key not in {"code", "name"}}
            return APIResponse(201, self.master.create_customer(principal, str(data["code"]), str(data["name"]), **fields))
        if path.startswith("/api/v1/customers/"):
            customer_id = path.split("/")[4]
            if method == "GET": return APIResponse(200, self.master.partner(principal, "customer", customer_id))
            if method == "PATCH":
                changes = {key: value for key, value in data.items() if key != "version"}
                return APIResponse(200, self.master.update_partner(principal, "customer", customer_id, int(data["version"]), **changes))
        if path == "/api/v1/suppliers" and method == "GET": return APIResponse(200, {"items": self.master.list_partners(principal, "supplier", query.get("q", ""))})
        if path == "/api/v1/suppliers" and method == "POST":
            fields = {key: value for key, value in data.items() if key not in {"code", "name"}}
            return APIResponse(201, self.master.create_supplier(principal, str(data["code"]), str(data["name"]), **fields))
        if path.startswith("/api/v1/suppliers/"):
            supplier_id = path.split("/")[4]
            if method == "GET": return APIResponse(200, self.master.partner(principal, "supplier", supplier_id))
            if method == "PATCH":
                changes = {key: value for key, value in data.items() if key != "version"}
                return APIResponse(200, self.master.update_partner(principal, "supplier", supplier_id, int(data["version"]), **changes))

        if path == "/api/v1/inventory/balances" and method == "GET":
            return APIResponse(200, {"items": self.inventory.list_balances(principal, query.get("site_id", ""), query.get("product_id", ""), query.get("low_stock", "false") == "true")})
        if path == "/api/v1/inventory/ledger" and method == "GET":
            return APIResponse(200, {"items": self.inventory.ledger(principal, query.get("product_id", ""), query.get("location_id", ""), query.get("reference_id", ""), self._integer(query, "limit", 200))})
        if path == "/api/v1/inventory/receive" and method == "POST":
            return self._idempotent(principal, method, path, headers, data, lambda: (201, self.inventory.receive(principal, str(data["product_id"]), str(data["location_id"]), int(data["quantity"]), str(data.get("event_key") or headers.get("idempotency-key", "")), str(data.get("lot_id", "")), int(data.get("unit_cost_cents", 0)), str(data.get("reference_type", "manual")), str(data.get("reference_id", "")), str(data.get("reason", "")))))
        if path == "/api/v1/inventory/transfers" and method == "POST":
            return self._idempotent(principal, method, path, headers, data, lambda: (201, self.inventory.transfer(principal, str(data["product_id"]), str(data["source_location_id"]), str(data["destination_location_id"]), int(data["quantity"]), str(data.get("event_key") or headers.get("idempotency-key", "")), str(data.get("lot_id", "")), str(data.get("reason", "")))))
        if path == "/api/v1/inventory/lots" and method == "POST":
            return APIResponse(201, self.inventory.create_lot(principal, str(data["product_id"]), str(data["lot_number"]), data.get("manufacture_date"), data.get("expiry_date"), data.get("supplier_id")))
        if path == "/api/v1/inventory/counts":
            if method == "GET": return APIResponse(200, {"items": self.inventory.list_counts(principal, query.get("status", ""), self._integer(query, "limit", 100))})
            if method == "POST": return APIResponse(201, self.inventory.create_count(principal, str(data["location_id"]), str(data["count_date"]), list(data["lines"]), str(data.get("reason", ""))))
        if path.startswith("/api/v1/inventory/counts/"):
            parts = path.split("/"); count_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.inventory.count(principal, count_id))
            if len(parts) == 7 and parts[6] == "post" and method == "POST": return APIResponse(200, self.inventory.post_count(principal, count_id))

        if path == "/api/v1/sales/orders" and method == "GET":
            return APIResponse(200, {"items": self.sales.list_orders(principal, query.get("status", ""), query.get("customer_id", ""), query.get("since", ""), query.get("until", ""), self._integer(query, "limit", 100), self._integer(query, "offset", 0))})
        if path == "/api/v1/sales/orders" and method == "POST":
            return self._idempotent(principal, method, path, headers, data, lambda: (201, self.sales.create_order(principal, str(data["customer_id"]), list(data["lines"]), data.get("order_date"), data.get("requested_delivery_date"), str(data.get("currency", "CNY")), int(data.get("freight_cents", 0)), str(data.get("shipping_address", "")), str(data.get("billing_address", "")), str(data.get("channel", "direct")), str(data.get("external_reference", "")), str(data.get("notes", "")))))
        if path.startswith("/api/v1/sales/orders/"):
            parts = path.split("/"); order_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.sales.order(principal, order_id))
            if len(parts) == 6 and method == "PATCH":
                return APIResponse(200, self.sales.update_draft(
                    principal, order_id, int(data["version"]), data.get("lines"), data.get("requested_delivery_date"),
                    int(data["freight_cents"]) if data.get("freight_cents") is not None else None,
                    data.get("shipping_address"), data.get("notes"),
                ))
            if len(parts) == 7 and method == "POST":
                action = parts[6]
                if action == "confirm": return APIResponse(200, self.sales.confirm(principal, order_id))
                if action == "reserve": return APIResponse(200, self.sales.reserve(principal, order_id, data.get("allocations")))
                if action == "cancel": return APIResponse(200, self.sales.cancel(principal, order_id, str(data.get("reason", ""))))
                if action == "shipments": return APIResponse(201, self.sales.create_shipment(principal, order_id, data.get("lines"), str(data.get("carrier", "")), str(data.get("tracking_number", ""))))
                if action == "returns": return APIResponse(201, self.sales.create_return(principal, order_id, str(data["reason_code"]), list(data["lines"]), str(data.get("reason_detail", "")), str(data.get("resolution", "refund"))))
        if path.startswith("/api/v1/sales/shipments/"):
            parts = path.split("/"); shipment_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.sales.shipment(principal, shipment_id))
            if len(parts) == 7 and parts[6] == "post" and method == "POST":
                return self._idempotent(principal, method, path, headers, data, lambda: (200, self.sales.post_shipment(principal, shipment_id, str(data.get("event_key") or headers.get("idempotency-key", "")))))
            if len(parts) == 7 and parts[6] == "cancel" and method == "POST":
                return APIResponse(200, self.sales.cancel_shipment(principal, shipment_id, str(data.get("reason", ""))))
        if path == "/api/v1/sales/returns" and method == "GET":
            return APIResponse(200, {"items": self.sales.list_returns(principal, query.get("status", ""), query.get("order_id", ""), self._integer(query, "limit", 100))})
        if path.startswith("/api/v1/sales/returns/"):
            parts = path.split("/"); return_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.sales.sales_return(principal, return_id))
            if len(parts) == 7 and parts[6] == "authorize" and method == "POST": return APIResponse(200, self.sales.authorize_return(principal, return_id, bool(data.get("approve", True))))
            if len(parts) == 7 and parts[6] == "receive" and method == "POST": return APIResponse(200, self.sales.receive_return(principal, return_id, str(data["location_id"]), str(data.get("event_key") or headers.get("idempotency-key", ""))))

        if path == "/api/v1/channels/overview" and method == "GET":
            return APIResponse(200, self.channels.overview(principal))
        if path == "/api/v1/channels/shops":
            if method == "GET": return APIResponse(200, {"items": self.channels.list_shops(principal)})
            if method == "POST": return APIResponse(201, self.channels.create_shop(
                principal, str(data["platform"]), str(data["code"]), str(data["name"]),
                str(data["settlement_customer_id"]), str(data["default_site_id"]), str(data["external_shop_id"]),
                str(data.get("currency", "CNY")), str(data.get("sync_mode", "pull_webhook")),
                str(data.get("credential_env", "")), str(data.get("webhook_secret_env", "")),
            ))
        if path == "/api/v1/channels/listings":
            if method == "GET": return APIResponse(200, {"items": self.channels.list_listings(principal, query.get("shop_id", ""))})
            if method == "POST": return APIResponse(201, self.channels.map_listing(
                principal, str(data["shop_id"]), str(data["external_product_id"]), str(data["external_sku_id"]),
                str(data.get("title", "")), list(data["components"]),
            ))
        if path == "/api/v1/channels/orders" and method == "GET":
            return APIResponse(200, {"items": self.channels.list_orders(principal, query.get("status", ""), query.get("shop_id", ""), self._integer(query, "limit", 200))})
        if path.startswith("/api/v1/channels/shops/"):
            parts = path.split("/"); shop_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.channels.shop(principal, shop_id))
            if len(parts) == 7 and parts[6] == "orders" and method == "POST":
                return APIResponse(200, self.channels.ingest_orders(principal, shop_id, list(data["orders"]), str(data.get("trigger_type", "manual")), str(data.get("cursor", ""))))
        if path.startswith("/api/v1/channels/orders/"):
            parts = path.split("/"); channel_order_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.channels.order(principal, channel_order_id))
            if len(parts) == 7 and method == "POST":
                if parts[6] == "review": return APIResponse(200, self.channels.review_and_import(principal, channel_order_id, bool(data.get("reserve", True))))
                if parts[6] == "cancel": return APIResponse(200, self.channels.cancel_order(principal, channel_order_id, str(data.get("reason", ""))))
                if parts[6] == "address": return APIResponse(200, self.channels.change_shipping_address(
                    principal, channel_order_id, str(data["recipient"]), str(data["phone"]), str(data["province"]),
                    str(data["city"]), str(data.get("district", "")), str(data["street"]),
                ))
        if path == "/api/v1/channels/callbacks" and method == "GET":
            return APIResponse(200, {"items": self.channels.list_callbacks(principal, query.get("status", ""))})
        if path.startswith("/api/v1/channels/callbacks/") and method == "POST":
            parts = path.split("/"); task_id = parts[5]
            if len(parts) == 7 and parts[6] == "complete": return APIResponse(200, self.channels.complete_callback(principal, task_id, bool(data.get("success")), str(data.get("error", ""))))

        if path == "/api/v1/purchases/orders" and method == "GET": return APIResponse(200, {"items": self.purchasing.list_orders(principal, query.get("status", ""), query.get("supplier_id", ""), self._integer(query, "limit", 100), self._integer(query, "offset", 0))})
        if path == "/api/v1/purchases/orders" and method == "POST":
            return self._idempotent(principal, method, path, headers, data, lambda: (201, self.purchasing.create_order(principal, str(data["supplier_id"]), str(data["warehouse_id"]), list(data["lines"]), data.get("order_date"), data.get("expected_date"), str(data.get("currency", "CNY")), int(data.get("freight_cents", 0)), str(data.get("supplier_reference", "")), str(data.get("notes", "")))))
        if path.startswith("/api/v1/purchases/orders/"):
            parts = path.split("/"); purchase_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.purchasing.order(principal, purchase_id))
            if len(parts) == 6 and method == "PATCH":
                return APIResponse(200, self.purchasing.update_draft(
                    principal, purchase_id, int(data["version"]), data.get("lines"), data.get("expected_date"),
                    int(data["freight_cents"]) if data.get("freight_cents") is not None else None,
                    data.get("supplier_reference"), data.get("notes"),
                ))
            if len(parts) == 7 and method == "POST":
                action = parts[6]
                if action == "submit": return APIResponse(200, self.purchasing.submit(principal, purchase_id))
                if action == "approve": return APIResponse(200, self.purchasing.approve(principal, purchase_id))
                if action == "reject": return APIResponse(200, self.purchasing.reject(principal, purchase_id, str(data.get("reason", ""))))
                if action == "cancel": return APIResponse(200, self.purchasing.cancel(principal, purchase_id, str(data.get("reason", ""))))
                if action == "receipts": return APIResponse(201, self.purchasing.create_receipt(principal, purchase_id, str(data["location_id"]), list(data["lines"]), data.get("receipt_date"), str(data.get("supplier_delivery_note", ""))))
        if path == "/api/v1/purchases/receipts" and method == "GET":
            return APIResponse(200, {"items": self.purchasing.list_receipts(principal, query.get("status", ""), query.get("purchase_id", ""), self._integer(query, "limit", 100))})
        if path.startswith("/api/v1/purchases/receipts/"):
            parts = path.split("/"); receipt_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.purchasing.receipt(principal, receipt_id))
            if len(parts) == 7 and parts[6] == "post" and method == "POST": return self._idempotent(principal, method, path, headers, data, lambda: (200, self.purchasing.post_receipt(principal, receipt_id, str(data.get("event_key") or headers.get("idempotency-key", "")))))

        if path == "/api/v1/finance/invoices" and method == "GET": return APIResponse(200, {"items": self.finance.list_invoices(principal, query.get("type", ""), query.get("status", ""), query.get("partner_id", ""), query.get("overdue", "false") == "true")})
        if path == "/api/v1/finance/journal-entries" and method == "GET":
            return APIResponse(200, {"items": self.ledger.list_entries(
                principal, query.get("since", ""), query.get("until", ""), query.get("source_type", ""),
                self._integer(query, "limit", 200),
            )})
        if path.startswith("/api/v1/finance/journal-entries/") and method == "GET":
            return APIResponse(200, self.ledger.entry(principal, path.split("/")[5]))
        if path == "/api/v1/finance/accounts" and method == "GET":
            return APIResponse(200, {"items": self.ledger.list_accounts(principal)})
        if path == "/api/v1/finance/general-ledger" and method == "GET":
            return APIResponse(200, self.ledger.account_statement(
                principal, query.get("account_code", ""), query.get("since", ""), query.get("until", ""),
                query.get("partner_id", ""), query.get("product_id", ""), self._integer(query, "limit", 1000),
            ))
        if path == "/api/v1/finance/trial-balance" and method == "GET":
            return APIResponse(200, self.ledger.trial_balance(principal, query.get("as_of") or None))
        if path == "/api/v1/finance/statements" and method == "GET":
            return APIResponse(200, self.ledger.financial_statements(principal, query.get("as_of") or None))
        if path == "/api/v1/finance/subledger-reconciliation" and method == "GET":
            return APIResponse(200, self.ledger.reconcile_subledgers(principal))
        if path == "/api/v1/finance/bank-accounts":
            if method == "GET":
                return APIResponse(200, {"items": self.cash.list_accounts(principal, query.get("active", "true") != "false")})
            if method == "POST":
                return APIResponse(201, self.cash.create_account(
                    principal, str(data["code"]), str(data["name"]), str(data["bank_name"]),
                    str(data.get("ledger_account_code", "1002")), str(data.get("currency", "CNY")),
                    str(data.get("account_number", "")), int(data.get("opening_balance_cents", 0)),
                ))
        if path.startswith("/api/v1/finance/bank-accounts/") and method == "GET":
            return APIResponse(200, self.cash.account(principal, path.split("/")[5]))
        if path == "/api/v1/finance/bank-statements":
            if method == "GET":
                return APIResponse(200, {"items": self.cash.list_statements(
                    principal, query.get("bank_account_id", ""), query.get("status", ""),
                    self._integer(query, "limit", 100),
                )})
            if method == "POST":
                return APIResponse(201, self.cash.import_statement(
                    principal, str(data["bank_account_id"]), str(data["statement_number"]),
                    str(data["period_start"]), str(data["period_end"]), int(data["opening_balance_cents"]),
                    int(data["closing_balance_cents"]), list(data["lines"]),
                ))
        if path.startswith("/api/v1/finance/bank-statements/"):
            parts = path.split("/"); statement_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.cash.statement(principal, statement_id))
            if len(parts) == 7 and parts[6] == "auto-match" and method == "POST":
                return APIResponse(200, self.cash.auto_match(principal, statement_id))
            if len(parts) == 7 and parts[6] == "reconcile" and method == "POST":
                return APIResponse(200, self.cash.reconcile(principal, statement_id))
        if path.startswith("/api/v1/finance/bank-statement-lines/"):
            parts = path.split("/"); line_id = parts[5]
            if len(parts) == 7 and parts[6] == "candidates" and method == "GET":
                return APIResponse(200, {"items": self.cash.match_candidates(
                    principal, line_id, self._integer(query, "date_tolerance_days", 3),
                )})
            if len(parts) == 7 and parts[6] == "match" and method == "POST":
                return APIResponse(200, self.cash.confirm_match(principal, line_id, str(data["payment_id"])))
            if len(parts) == 7 and parts[6] == "unmatch" and method == "POST":
                return APIResponse(200, self.cash.unmatch(principal, line_id, str(data.get("reason", ""))))
        if path == "/api/v1/finance/invoices/from-sales" and method == "POST": return APIResponse(201, self.finance.create_invoice_from_sales(principal, str(data["sales_document_id"]), data.get("invoice_date"), data.get("due_date"), str(data.get("notes", ""))))
        if path == "/api/v1/finance/invoices/from-purchase" and method == "POST":
            return self._idempotent(principal, method, path, headers, data, lambda: (201,
                self.finance.create_invoice_from_purchase(
                    principal, str(data["purchase_order_id"]), data.get("invoice_date"), data.get("due_date"),
                    str(data.get("notes", "")), str(data.get("supplier_invoice_number", "")),
                    data.get("lines"), int(data.get("price_tolerance_basis_points", 0)),
                    int(data["supplier_total_cents"]) if data.get("supplier_total_cents") is not None else None,
                )))
        if path == "/api/v1/finance/payments":
            if method == "GET": return APIResponse(200, {"items": self.finance.list_payments(principal, query.get("type", ""), query.get("status", ""), query.get("partner_id", ""), self._integer(query, "limit", 200))})
            if method == "POST": return self._idempotent(principal, method, path, headers, data, lambda: (201, self.finance.record_payment(principal, str(data["payment_type"]), str(data["partner_type"]), str(data["partner_id"]), int(data["amount_cents"]), data.get("payment_date"), str(data.get("currency", "CNY")), str(data.get("method", "bank_transfer")), str(data.get("external_reference", "")), list(data.get("allocations", [])), str(data.get("notes", "")), str(data.get("bank_account_id", "")))))
        if path.startswith("/api/v1/finance/invoices/"):
            parts = path.split("/"); invoice_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.finance.invoice(principal, invoice_id))
            if len(parts) == 7 and parts[6] == "void" and method == "POST":
                return APIResponse(200, self.finance.void_invoice(principal, invoice_id, str(data.get("reason", ""))))
        if path.startswith("/api/v1/finance/payments/"):
            parts = path.split("/"); payment_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.finance.payment(principal, payment_id))
            if len(parts) == 7 and parts[6] == "void" and method == "POST":
                return APIResponse(200, self.finance.void_payment(principal, payment_id, str(data.get("reason", ""))))
        if path == "/api/v1/finance/periods" and method == "GET": return APIResponse(200, {"items": self.finance.list_periods(principal, self._integer(query, "limit", 120))})
        if path == "/api/v1/finance/periods/close" and method == "POST": return APIResponse(200, self.finance.close_period(principal, int(data["year"]), int(data["month"])))
        if path == "/api/v1/finance/periods/reopen" and method == "POST": return APIResponse(200, self.finance.reopen_period(principal, int(data["year"]), int(data["month"]), str(data.get("reason", ""))))

        if path == "/api/v1/reports/sales" and method == "GET": return APIResponse(200, {"items": self.reports.sales_summary(principal, query["since"], query["until"], query.get("group_by", "day"))})
        if path == "/api/v1/reports/inventory-valuation" and method == "GET": return APIResponse(200, {"items": self.reports.inventory_valuation(principal, query.get("site_id", ""))})
        if path == "/api/v1/reports/ar-aging" and method == "GET": return APIResponse(200, self.reports.ar_aging(principal, query.get("as_of")))
        if path == "/api/v1/reports/reorder" and method == "GET": return APIResponse(200, {"items": self.reports.reorder_suggestions(principal, query.get("site_id", ""))})
        if path == "/api/v1/audit" and method == "GET": return APIResponse(200, {"items": self.audit.search(principal, query.get("entity_type", ""), query.get("entity_id", ""), query.get("actor_id", ""), query.get("action", ""), query.get("since", ""), query.get("until", ""), self._integer(query, "limit", 100), self._integer(query, "offset", 0))})

        if path == "/api/v1/pricing/lists" and method == "GET":
            return APIResponse(200, {"items": self.pricing.list_price_lists(principal, query.get("active", "true") != "false")})
        if path == "/api/v1/pricing/lists" and method == "POST":
            return APIResponse(201, self.pricing.create_price_list(
                principal, str(data["code"]), str(data["name"]), str(data.get("currency", "CNY")),
                str(data.get("customer_id") or "") or None, str(data.get("channel", "")),
                data.get("valid_from"), data.get("valid_until"), int(data.get("priority", 100))
            ))
        if path.startswith("/api/v1/pricing/lists/"):
            parts = path.split("/"); price_list_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.pricing.price_list(principal, price_list_id))
            if len(parts) == 7 and parts[6] == "rules" and method == "POST":
                return APIResponse(201, self.pricing.add_rule(
                    principal, price_list_id, str(data["product_id"]), int(data["min_quantity"]),
                    int(data["unit_price_cents"]), int(data.get("discount_basis_points", 0)),
                    data.get("valid_from"), data.get("valid_until")
                ))
        if path == "/api/v1/pricing/resolve" and method == "POST":
            return APIResponse(200, self.pricing.resolve(
                principal, str(data["product_id"]), int(data["quantity"]),
                str(data.get("customer_id") or "") or None, str(data.get("channel", "")),
                data.get("pricing_date"), str(data.get("currency", "CNY"))
            ))

        if path == "/api/v1/inventory/serials" and method == "GET":
            return APIResponse(200, {"items": self.serials.list(
                principal, query.get("product_id", ""), query.get("status", ""),
                query.get("location_id", ""), self._integer(query, "limit", 500)
            )})
        if path == "/api/v1/inventory/serials" and method == "POST":
            return APIResponse(201, {"items": self.serials.register(
                principal, str(data["product_id"]), [str(value) for value in data["serial_numbers"]],
                str(data["location_id"]), str(data.get("lot_id") or "") or None
            )})
        if path.startswith("/api/v1/inventory/serials/"):
            parts = path.split("/"); serial_id = parts[5]
            if len(parts) == 6 and method == "GET": return APIResponse(200, self.serials.get(principal, serial_id))
            if len(parts) == 7 and parts[6] == "transition" and method == "POST":
                return APIResponse(200, self.serials.transition(
                    principal, serial_id, str(data["status"]),
                    str(data.get("location_id") or "") or None, str(data.get("reason", ""))
                ))

        if path == "/api/v1/imports/validate" and method == "POST":
            return APIResponse(201, self.imports.validate_csv(
                principal, str(data["import_type"]), str(data["content"]), str(data.get("filename", ""))
            ))
        if path.startswith("/api/v1/imports/"):
            parts = path.split("/"); job_id = parts[4]
            if len(parts) == 5 and method == "GET":
                return APIResponse(200, self.imports.job(principal, job_id, query.get("rows", "false") == "true"))
            if len(parts) == 6 and parts[5] == "commit" and method == "POST":
                return APIResponse(200, self.imports.commit(principal, job_id))
        if path.startswith("/api/v1/exports/") and method == "GET":
            content = self.imports.export_csv(principal, path.split("/")[4])
            return APIResponse(200, content, {"Content-Disposition": "attachment"}, "text/csv; charset=utf-8")

        if path == "/api/v1/reconciliations" and method == "GET":
            return APIResponse(200, {"items": self.reconciliation.list(principal, self._integer(query, "limit", 100))})
        if path == "/api/v1/reconciliations/run" and method == "POST":
            target = str(data.get("type", "all"))
            runners = {"inventory": self.reconciliation.run_inventory, "sales": self.reconciliation.run_sales,
                       "finance": self.reconciliation.run_finance, "accounting": self.reconciliation.run_accounting,
                       "all": self.reconciliation.run_all}
            if target not in runners: raise ValidationError("对账类型无效")
            return APIResponse(201, runners[target](principal))

        if path == "/api/v1/alerts" and method == "GET":
            return APIResponse(200, {"items": self.alerts.list(
                principal, query.get("status", ""), query.get("severity", ""), self._integer(query, "limit", 200)
            )})
        if path == "/api/v1/alerts/refresh" and method == "POST":
            return APIResponse(200, self.alerts.refresh(principal))
        if path.startswith("/api/v1/alerts/"):
            parts = path.split("/"); alert_id = parts[4]
            if len(parts) == 5 and method == "GET": return APIResponse(200, self.alerts.get(principal, alert_id))
            if len(parts) == 6 and parts[5] == "acknowledge" and method == "POST":
                return APIResponse(200, self.alerts.acknowledge(principal, alert_id))
            if len(parts) == 6 and parts[5] == "dismiss" and method == "POST":
                return APIResponse(200, self.alerts.dismiss(principal, alert_id, str(data.get("reason", ""))))

        raise NotFound(f"接口不存在：{method} {path}")

    def _run_delivery_task(self, task_id: str, actor: str = "system") -> dict:
        task = self.tasks.get(task_id)
        if task["status"] not in {"queued", "rework"}:
            raise Conflict("只有 queued 或 rework 任务可以启动")
        if task.get("automation_mode") == "automatic":
            return self.automation.start(task_id, actor)
        if task.get("execution_mode") == "codex":
            return run_task(
                self.tasks, task_id, actor,
                execution_runner=self.automation.execution_runner,
            )
        return run_task(self.tasks, task_id, actor)

    def _run_delivery_task_response(self, task_id: str, actor: str) -> tuple[int, dict]:
        task = self.tasks.get(task_id)
        status = 202 if task.get("automation_mode") == "automatic" else 200
        return status, self._run_delivery_task(task_id, actor)

    def _principal(self, headers: dict[str, str]) -> tuple[Principal, bool]:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return self.identity.authenticate(auth[7:].strip()), False
        cookies = self._cookies(headers.get("cookie", ""))
        if cookies.get("flowerp_session"):
            return self.identity.authenticate(cookies["flowerp_session"]), True
        if not self.settings.auth_required:
            return SYSTEM_PRINCIPAL, False
        raise AuthenticationError("请先登录")

    def _runtime_metrics(self) -> str:
        with self._metric_lock:
            values = {
                "flowerp_http_requests_total": self._requests_total,
                "flowerp_http_responses_5xx_total": self._responses_5xx_total,
                "flowerp_http_rate_rejections_total": self._rate_rejections_total,
                "flowerp_http_overload_rejections_total": self._overload_rejections_total,
                "flowerp_http_request_duration_seconds_total": round(self._request_duration_seconds, 6),
            }
        lines: list[str] = []
        for name, value in values.items():
            lines.extend((f"# TYPE {name} counter", f"{name} {value}"))
        return "\n".join(lines) + "\n"

    def _idempotent(self, principal: Principal, method: str, path: str, headers: dict[str, str],
                    body: object, operation) -> APIResponse:
        key = headers.get("idempotency-key", "").strip()
        if not key: raise ValidationError("写入接口必须提供 Idempotency-Key")
        request_hash = self.idempotency.request_hash(method, path, body)
        cached = self.idempotency.begin(principal.organization_id, path, key, request_hash)
        if cached: return APIResponse(cached["status"], cached["body"], {"Idempotent-Replay": "true"})
        try:
            status, result = operation()
            self.idempotency.complete(principal.organization_id, path, key, status, result)
            return APIResponse(status, result, {"Idempotent-Replay": "false"})
        except Exception:
            self.idempotency.abandon(principal.organization_id, path, key)
            raise

    @staticmethod
    def _cookies(value: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for part in value.split(";"):
            key, sep, item = part.strip().partition("=")
            if sep: result[key] = item
        return result

    @staticmethod
    def _principal_json(principal: Principal) -> dict:
        return {"id": principal.user_id, "organization_id": principal.organization_id, "username": principal.username,
                "display_name": principal.display_name, "permissions": sorted(principal.permissions)}

    @staticmethod
    def _integer(query: dict[str, str], key: str, default: int) -> int:
        raw = query.get(key)
        return default if raw in {None, ""} else int(raw)

    def _check_login_rate(self, remote_addr: str) -> None:
        """Allow at most 20 login attempts per source address in five minutes."""
        key = remote_addr or "unknown"
        now = time.monotonic()
        with self._rate_lock:
            attempts = self._login_attempts[key]
            while attempts and attempts[0] < now - 300:
                attempts.popleft()
            if len(attempts) >= 20:
                raise PermissionDenied("登录尝试过于频繁，请稍后再试")
            attempts.append(now)

    def _check_request_rate(self, remote_addr: str) -> bool:
        key = remote_addr or "unknown"
        now = time.monotonic()
        with self._rate_lock:
            attempts = self._request_attempts[key]
            while attempts and attempts[0] < now - 60:
                attempts.popleft()
            if len(attempts) >= self.settings.request_rate_per_minute:
                return False
            attempts.append(now)
            return True
