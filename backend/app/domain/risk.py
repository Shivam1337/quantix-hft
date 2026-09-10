from dataclasses import dataclass

from app.domain.types import RiskDecision


@dataclass(frozen=True)
class RiskConfig:
    basis_threshold_bps: float = 200
    negative_hours_to_unwind: int = 2
    auto_unwind: bool = True


class RiskEngine:
    def __init__(self, config: RiskConfig):
        self.config = config

    def evaluate(
        self,
        net_apr_pct: float | None,
        basis_bps: float,
        negative_hours: int,
        config: RiskConfig | None = None,
    ) -> RiskDecision:
        active = config or self.config
        basis_breach = abs(basis_bps) > active.basis_threshold_bps
        funding_flip = (
            negative_hours >= active.negative_hours_to_unwind
            and net_apr_pct is not None
            and net_apr_pct < 0
        )
        reason = None
        if basis_breach:
            reason = (
                f"basis divergence {basis_bps:.2f} bps exceeds "
                f"{active.basis_threshold_bps:.2f} bps"
            )
        elif funding_flip:
            reason = f"negative net APR persisted for {negative_hours} funding hours"
        return RiskDecision(
            should_unwind=active.auto_unwind and (basis_breach or funding_flip),
            basis_breach=basis_breach,
            funding_flip=funding_flip,
            reason=reason,
        )
