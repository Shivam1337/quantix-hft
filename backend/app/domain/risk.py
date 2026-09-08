from dataclasses import dataclass

from app.domain.types import RiskDecision


@dataclass(frozen=True)
class RiskConfig:
    basis_threshold_bps: float = 75
    negative_hours_to_unwind: int = 2
    auto_unwind: bool = True


class RiskEngine:
    def __init__(self, config: RiskConfig):
        self.config = config

    def evaluate(self, net_apr_pct: float, basis_bps: float, negative_hours: int) -> RiskDecision:
        basis_breach = abs(basis_bps) > self.config.basis_threshold_bps
        funding_flip = negative_hours >= self.config.negative_hours_to_unwind and net_apr_pct < 0
        reason = None
        if basis_breach:
            reason = (
                f"basis divergence {basis_bps:.2f} bps exceeds "
                f"{self.config.basis_threshold_bps:.2f} bps"
            )
        elif funding_flip:
            reason = f"negative net APR persisted for {negative_hours} funding hours"
        return RiskDecision(
            should_unwind=self.config.auto_unwind and (basis_breach or funding_flip),
            basis_breach=basis_breach,
            funding_flip=funding_flip,
            reason=reason,
        )
