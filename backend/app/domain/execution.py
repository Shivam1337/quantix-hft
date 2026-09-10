import uuid
from dataclasses import dataclass, replace

from app.domain.fees import FeeSchedule
from app.domain.types import OpportunityData


@dataclass(frozen=True)
class PaperExecutionConfig:
    """Conservative, deterministic assumptions for paper fills."""

    slippage_bps: float = 1.0
    post_only_fill_ratio: float = 0.90


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
    requested_size_usd: float = 0.0
    fill_ratio: float = 1.0
    status: str = "filled"
    symbol: str = ""


@dataclass(frozen=True)
class ExecutionResult:
    paper: bool
    legs: tuple[ExecutionLeg, ExecutionLeg]
    requested_size_usd: float = 0.0
    temporary_exposure_usd: float = 0.0

    @property
    def matched_size_usd(self) -> float:
        return min(leg.size_usd for leg in self.legs)

    @property
    def is_partial(self) -> bool:
        return self.matched_size_usd + 1e-9 < self.requested_size_usd


class ExecutionManager:
    """Deterministic paper execution with book-aware prices and conservative fills."""

    def __init__(
        self,
        live_trading_enabled: bool = False,
        fee_schedule: FeeSchedule | None = None,
        paper_config: PaperExecutionConfig | None = None,
    ):
        self.live_trading_enabled = live_trading_enabled
        self.fee_schedule = fee_schedule or FeeSchedule()
        self.paper_config = paper_config or PaperExecutionConfig()

    def open_pair(
        self, opportunity: OpportunityData, size_usd: float, paper: bool = True
    ) -> ExecutionResult:
        self._validate_size(size_usd)
        if size_usd > opportunity.capacity_usd:
            raise ValueError(
                f"size_usd exceeds opportunity capacity of ${opportunity.capacity_usd:,.2f}"
            )
        self._validate_mode(paper)
        return self._build_pair(
            opportunity, size_usd, paper, "open", ("buy", "sell"), allow_partial=True
        )

    def close_pair(
        self, opportunity: OpportunityData, size_usd: float, paper: bool = True
    ) -> ExecutionResult:
        self._validate_size(size_usd)
        self._validate_mode(paper)
        return self._build_pair(
            opportunity, size_usd, paper, "close", ("sell", "buy"), allow_partial=True
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
        self._validate_size(size_usd)
        self._validate_mode(paper)
        mode = "paper" if paper else "live"
        first = self._leg(
            long_venue, "sell", long_price, long_price, long_price, size_usd,
            mode, "close", symbol
        )
        second = self._leg(
            short_venue, "buy", short_price, short_price, short_price, first.size_usd,
            mode, "close", symbol
        )
        return self._match_result(first, second, size_usd, paper)

    def _build_pair(
        self,
        opportunity: OpportunityData,
        size_usd: float,
        paper: bool,
        phase: str,
        sides: tuple[str, str],
        allow_partial: bool,
    ) -> ExecutionResult:
        mode = "paper" if paper else "live"
        first = self._leg(
            opportunity.long_venue,
            sides[0],
            opportunity.long_mark_price,
            opportunity.long_bid_price or opportunity.long_mark_price,
            opportunity.long_ask_price or opportunity.long_mark_price,
            size_usd,
            mode,
            phase,
            opportunity.symbol,
            fill_ratio_override=(None if allow_partial else 1.0),
        )
        second = self._leg(
            opportunity.short_venue,
            sides[1],
            opportunity.short_mark_price,
            opportunity.short_bid_price or opportunity.short_mark_price,
            opportunity.short_ask_price or opportunity.short_mark_price,
            first.size_usd,
            mode,
            phase,
            opportunity.symbol,
            fill_ratio_override=(None if allow_partial else 1.0),
        )
        return self._match_result(first, second, size_usd, paper)

    def _match_result(
        self,
        first: ExecutionLeg,
        second: ExecutionLeg,
        requested_size_usd: float,
        paper: bool,
    ) -> ExecutionResult:
        matched = min(first.size_usd, second.size_usd)
        first_fill = first.size_usd
        first = self._resize_leg(first, matched, requested_size_usd)
        second = self._resize_leg(second, matched, second.requested_size_usd)
        return ExecutionResult(
            paper=paper,
            legs=(first, second),
            requested_size_usd=requested_size_usd,
            # The first leg is live before the hedge order is acknowledged.
            temporary_exposure_usd=first_fill,
        )

    def _leg(
        self,
        venue: str,
        side: str,
        mark_price: float,
        bid: float,
        ask: float,
        requested_size_usd: float,
        mode: str,
        phase: str,
        symbol: str = "",
        fill_ratio_override: float | None = None,
    ) -> ExecutionLeg:
        paper = mode == "paper"
        order_type = self.fee_schedule.order_type_for(venue)
        fill_ratio = (
            fill_ratio_override
            if fill_ratio_override is not None
            else
            self.paper_config.post_only_fill_ratio
            if paper and order_type == "post_only_limit"
            else 1.0
        )
        slippage = self.paper_config.slippage_bps / 10_000 if paper else 0.0
        if order_type == "post_only_limit":
            price = bid if side == "buy" else ask
        elif side == "buy":
            price = ask * (1 + slippage)
        else:
            price = bid * (1 - slippage)
        filled_size = requested_size_usd * fill_ratio
        fee_bps = self.fee_schedule.rate_for(venue, order_type)
        return ExecutionLeg(
            venue=venue,
            side=side,
            price=price or mark_price,
            size_usd=filled_size,
            order_type=order_type,
            client_order_id=f"{phase}-{mode}-{uuid.uuid4().hex[:16]}",
            phase=phase,
            fee_bps=fee_bps,
            fee_usd=filled_size * fee_bps / 10_000,
            requested_size_usd=requested_size_usd,
            fill_ratio=fill_ratio,
            status=(
                "paper_partial"
                if paper and fill_ratio < 1
                else ("paper_filled" if paper else "filled")
            ),
            symbol=symbol,
        )

    def _resize_leg(
        self, leg: ExecutionLeg, size_usd: float, requested_size_usd: float
    ) -> ExecutionLeg:
        return replace(
            leg,
            size_usd=size_usd,
            fee_usd=size_usd * leg.fee_bps / 10_000,
            requested_size_usd=requested_size_usd,
            fill_ratio=size_usd / requested_size_usd if requested_size_usd else 0.0,
        )

    @staticmethod
    def _validate_size(size_usd: float) -> None:
        if size_usd <= 0:
            raise ValueError("size_usd must be greater than zero")

    def _validate_mode(self, paper: bool) -> None:
        if not paper and not self.live_trading_enabled:
            raise ValueError("live trading is disabled; enable LIVE_TRADING_ENABLED explicitly")
