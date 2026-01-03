from __future__ import annotations

from typing import Protocol

from src.domain.models import AccountSummaryItem, OpenOrderRow, PositionRow


class BrokerPort(Protocol):
    async def get_account_summary(self) -> list[AccountSummaryItem]: ...

    async def get_positions(self) -> list[PositionRow]: ...

    async def get_open_orders(self) -> list[OpenOrderRow]: ...




