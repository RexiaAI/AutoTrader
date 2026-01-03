from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AccountSummaryItem:
    tag: str
    value: float
    currency: str

    def to_dict(self) -> dict[str, Any]:
        return {"tag": self.tag, "value": float(self.value), "currency": self.currency}


@dataclass(frozen=True)
class PositionRow:
    symbol: str
    exchange: str
    currency: str
    position: int
    avg_cost: float | None
    market_price: float | None
    market_value: float | None
    unrealised_pnl: float | None
    realised_pnl: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "currency": self.currency,
            "position": int(self.position),
            "avg_cost": self.avg_cost,
            "market_price": self.market_price,
            "market_value": self.market_value,
            "unrealised_pnl": self.unrealised_pnl,
            "realised_pnl": self.realised_pnl,
        }


@dataclass(frozen=True)
class OpenOrderRow:
    order_id: int
    symbol: str
    exchange: str
    currency: str
    action: str
    order_type: str
    total_qty: float
    filled: float
    remaining: float
    status: str
    lmt_price: float
    aux_price: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": int(self.order_id),
            "symbol": self.symbol,
            "exchange": self.exchange,
            "currency": self.currency,
            "action": self.action,
            "order_type": self.order_type,
            "total_qty": float(self.total_qty),
            "filled": float(self.filled),
            "remaining": float(self.remaining),
            "status": self.status,
            "lmt_price": float(self.lmt_price),
            "aux_price": float(self.aux_price),
        }




