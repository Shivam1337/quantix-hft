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
        return "waiting for confirmed funding history"
    if item.historical_snapshots_count < settings.entry_min_history_snapshots:
        return (
            f"only {item.historical_snapshots_count} historical snapshots; "
            f"need {settings.entry_min_history_snapshots}"
        )
    if item.historical_3d_apr_pct <= 0:
        return "3-day historical APR is not positive"
    recent_net = getattr(item, "recent_net_hourly_rate", None)
    if recent_net is None:
        recent_net = item.net_hourly_rate
    if recent_net <= 0:
        return "recent funding spread does not recover fees"
    expected_hours = getattr(settings, "entry_expected_holding_hours", 24.0)
    if item.fee_breakeven_hours is None or item.fee_breakeven_hours >= expected_hours:
        return (
            f"fees require {item.fee_breakeven_hours or 0:.1f} hours; "
            f"expected holding horizon is {expected_hours:.1f} hours"
        )
    recent_stability = getattr(item, "recent_spread_stability_pct", None)
    if recent_stability is not None and recent_stability < settings.entry_min_spread_stability_pct:
        return "recent funding spread stability is below the configured floor"
    if item.spread_stability_pct is None:
        return "waiting for funding spread stability history"
    if item.spread_stability_pct < settings.entry_min_spread_stability_pct:
        return "funding spread stability is below the configured floor"
    return None


def simulation_entry_rejection_reason(account, settings) -> str | None:
    """Enforce explicit capital and drawdown guardrails for autonomous entries."""
    minimum = getattr(settings, "simulation_min_capital_usd", 100.0)
    if account.current_balance < minimum:
        return f"balance ${account.current_balance:.2f} is below minimum capital ${minimum:.2f}"
    if account.initial_balance <= 0:
        return "starting balance must be positive"
    drawdown_pct = max(
        0.0,
        (account.initial_balance - account.current_balance) / account.initial_balance * 100,
    )
    limit = getattr(settings, "simulation_max_drawdown_pct", 25.0)
    if drawdown_pct > limit:
        return f"drawdown {drawdown_pct:.2f}% exceeds policy limit {limit:.2f}%"
    return None
