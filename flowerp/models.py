from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from decimal import Decimal, ROUND_HALF_UP


class DomainError(RuntimeError):
    """Base class for business-rule failures."""


class NotFound(DomainError):
    pass


class InsufficientStock(DomainError):
    def __init__(self, sku: str, requested: int, available: int) -> None:
        self.sku = sku
        self.requested = requested
        self.available = available
        super().__init__(f"SKU {sku} 库存不足：需要 {requested}，可用 {available}，缺口 {requested - available}")


class InvalidTransition(DomainError):
    pass


class ApprovalRequired(DomainError):
    pass


class Conflict(DomainError):
    """The requested write conflicts with current persisted state."""


class ValidationError(DomainError):
    """Structured input failed domain validation."""


class PermissionDenied(DomainError):
    """The current principal cannot perform the operation."""


class AuthenticationError(DomainError):
    """Credentials are absent, invalid, expired, or revoked."""


class CreditLimitExceeded(DomainError):
    pass


class PeriodClosed(DomainError):
    pass


class OrderStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    RESERVED = "reserved"
    PARTIALLY_SHIPPED = "partially_shipped"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class PurchaseStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    RECEIVED = "received"
    REJECTED = "rejected"


class PurchaseOrderStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class InvoiceStatus(str, Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    VOID = "void"


class StockDocumentStatus(str, Enum):
    DRAFT = "draft"
    POSTED = "posted"
    CANCELLED = "cancelled"


class PartnerStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ProductTracking(str, Enum):
    NONE = "none"
    LOT = "lot"
    SERIAL = "serial"


class UserStatus(str, Enum):
    ACTIVE = "active"
    LOCKED = "locked"
    DISABLED = "disabled"


class Role(str, Enum):
    ADMIN = "admin"
    SALES = "sales"
    PURCHASING = "purchasing"
    WAREHOUSE = "warehouse"
    FINANCE = "finance"
    AUDITOR = "auditor"


@dataclass(frozen=True)
class OrderLine:
    sku: str
    quantity: int
    unit_price_cents: int

    @property
    def line_total_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass(frozen=True)
class Money:
    """Integer minor-unit money value with explicit ISO currency."""

    amount: int
    currency: str = "CNY"

    def __post_init__(self) -> None:
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValidationError("币种必须是三位 ISO 代码")

    def __add__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def _same_currency(self, other: "Money") -> None:
        if self.currency.upper() != other.currency.upper():
            raise ValidationError("不同币种不能直接计算")


def calculate_tax(net_cents: int, rate_basis_points: int) -> int:
    """Calculate tax using commercial half-up rounding."""
    if net_cents < 0 or not 0 <= rate_basis_points <= 10000:
        raise ValidationError("金额或税率超出范围")
    value = Decimal(net_cents) * Decimal(rate_basis_points) / Decimal(10000)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def require_positive(value: int, label: str = "数量") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{label}必须为正整数")
    return value


def require_non_negative(value: int, label: str = "数值") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"{label}不能为负")
    return value


def validate_iso_date(value: str, label: str = "日期") -> str:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label}必须使用 YYYY-MM-DD 格式") from exc
    return value
