from app.domain.types import OpportunityData


def entry_rejection_reason(item: OpportunityData, settings) -> str | None:
    """Apply conservative autonomous-entry checks to a ranked opportunity."""
    if item.net_apr_pct < settings.min_apr:
        return f"current APR {item.net_apr_pct:.2f}% is below {settings.min_apr:.2f}%"
    if item.min_open_interest < settings.min_open_interest:
        return "open interest is below the configured floor"
    if abs(item.basis_bps) > settings.basis_threshold_bps:
        return "basis exceeds the configured safety threshold"
    if item.historical_3d_apr_pct is None:
        return "waiting for 3-day funding history"
    if item.historical_snapshots_count < settings.entry_min_history_snapshots:
        return (
            f"only {item.historical_snapshots_count} historical snapshots; "
            f"need {settings.entry_min_history_snapshots}"
        )
    if item.historical_3d_apr_pct <= 0:
        return "3-day historical APR is not positive"
    if item.spread_stability_pct is None:
        return "waiting for funding spread stability history"
    if item.spread_stability_pct < settings.entry_min_spread_stability_pct:
        return "funding spread stability is below the configured floor"
    if item.net_apr_pct > item.historical_3d_apr_pct * settings.entry_max_apr_ratio:
        return (
            f"current APR {item.net_apr_pct:.2f}% is more than "
            f"{settings.entry_max_apr_ratio:.1f}x its {item.historical_3d_apr_pct:.2f}% history"
        )
    return None
