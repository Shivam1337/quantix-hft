from dataclasses import dataclass, field


@dataclass(frozen=True)
class VenueFee:
    maker_bps: float
    taker_bps: float


@dataclass(frozen=True)
class FeeSchedule:
    venues: dict[str, VenueFee] = field(default_factory=lambda: {
        "hyperliquid": VenueFee(maker_bps=1.5, taker_bps=1.5),
        "aevo": VenueFee(maker_bps=5.0, taker_bps=8.0),
        "lighter": VenueFee(maker_bps=0.0, taker_bps=0.0),
    })
    order_types: dict[str, str] = field(default_factory=lambda: {
        "hyperliquid": "post_only_limit",
        "aevo": "market",
        "lighter": "market",
    })

    def order_type_for(self, venue: str) -> str:
        return self.order_types.get(venue, "market")

    def rate_for(self, venue: str, order_type: str) -> float:
        fee = self.venues.get(venue, VenueFee(maker_bps=0.0, taker_bps=0.0))
        return fee.maker_bps if order_type == "post_only_limit" else fee.taker_bps

    def pair_fee_bps(self, long_venue: str, short_venue: str) -> float:
        return self.rate_for(long_venue, self.order_type_for(long_venue)) + self.rate_for(
            short_venue, self.order_type_for(short_venue)
        )
