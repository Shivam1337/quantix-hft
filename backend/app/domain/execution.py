import uuid
from dataclasses import dataclass

from app.domain.fees import FeeSchedule
from app.domain.types import OpportunityData


@dataclass(frozen=True)
class ExecutionLeg:
    venue: str
    side: str
    price: float
    size_usd: float
    order_type: str
    client_order_id: str
    phase: str
    fee_bps: float
    fee_usd: float
    symbol: str = ""


@dataclass(frozen=True)
class ExecutionResult:
    paper: bool
    legs: tuple[ExecutionLeg, ExecutionLeg]


class ExecutionManager:
    """Paper execution now; live wallet/signature adapters are an explicit later boundary."""

    def __init__(
        self, live_trading_enabled: bool = False, fee_schedule: FeeSchedule | None = None
    ):
        self.live_trading_enabled = live_trading_enabled
        self.fee_schedule = fee_schedule or FeeSchedule()

    def open_pair(
        self, opportunity: OpportunityData, size_usd: float, paper: bool = True
    ) -> ExecutionResult:
        if size_usd <= 0:
            raise ValueError("size_usd must be greater than zero")
        if size_usd > opportunity.capacity_usd:
            raise ValueError(
                f"size_usd exceeds opportunity capacity of ${opportunity.capacity_usd:,.2f}"
            )
        self._validate_mode(paper)
        return self._build_pair(
            opportunity,
            size_usd,
            paper,
            phase="open",
            sides=("buy", "sell"),
        )

    def close_pair(
        self, opportunity: OpportunityData, size_usd: float, paper: bool = True
    ) -> ExecutionResult:
        if size_usd <= 0:
            raise ValueError("size_usd must be greater than zero")
        self._validate_mode(paper)
        return self._build_pair(
            opportunity,
            size_usd,
            paper,
            phase="close",
            sides=("sell", "buy"),
        )

    def close_pair_for_venues(
        self,
        long_venue: str,
        short_venue: str,
        long_price: float,
        short_price: float,
        size_usd: float,
        paper: bool = True,
        symbol: str = "",
    ) -> ExecutionResult:
        if size_usd <= 0:
            raise ValueError("size_usd must be greater than zero")
        self._validate_mode(paper)
        mode = "paper" if paper else "live"
        return ExecutionResult(
            paper=paper,
            legs=(
                self._leg(long_venue, "sell", long_price, size_usd, mode, "close", symbol=symbol),
                self._leg(short_venue, "buy", short_price, size_usd, mode, "close", symbol=symbol),
            ),
        )

    def _build_pair(
        self,
        opportunity: OpportunityData,
        size_usd: float,
        paper: bool,
        phase: str,
        sides: tuple[str, str],
    ) -> ExecutionResult:
        mode = "paper" if paper else "live"
        return ExecutionResult(
            paper=paper,
            legs=(
                self._leg(
                    opportunity.long_venue,
                    sides[0],
                    opportunity.long_mark_price,
                    size_usd,
                    mode,
                    phase,
                    symbol=opportunity.symbol,
                ),
                self._leg(
                    opportunity.short_venue,
                    sides[1],
                    opportunity.short_mark_price,
                    size_usd,
                    mode,
                    phase,
                    symbol=opportunity.symbol,
                ),
            ),
        )

    def _leg(
        self,
        venue: str,
        side: str,
        price: float,
        size_usd: float,
        mode: str,
        phase: str,
        symbol: str = "",
    ) -> ExecutionLeg:
        order_type = self.fee_schedule.order_type_for(venue)
        fee_bps = self.fee_schedule.rate_for(venue, order_type)
        return ExecutionLeg(
            venue=venue,
            side=side,
            price=price,
            size_usd=size_usd,
            order_type=order_type,
            client_order_id=f"{phase}-{mode}-{uuid.uuid4().hex[:16]}",
            phase=phase,
            fee_bps=fee_bps,
            fee_usd=size_usd * fee_bps / 10_000,
            symbol=symbol,
        )

    def _validate_mode(self, paper: bool) -> None:
        if not paper and not self.live_trading_enabled:
            raise ValueError("live trading is disabled; enable LIVE_TRADING_ENABLED explicitly")
