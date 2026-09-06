"""Summarize legacy append-only JSONL evidence from a lead-lag measurement run.

Usage:
    python analyze_experiment.py data/lead_lag_experiment_*.jsonl

New application runs persist derived state to PostgreSQL and intentionally do
not create raw quote logs. Use ``python inspect_postgres.py`` for current runs.
"""
from __future__ import annotations

import argparse
import json
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List


FORWARD_HORIZONS_MS = (50, 100, 250, 500, 1000, 2000)


def read_records(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid JSON in {path}:{line_number}: {error}") from error
    return records


def average(values: Iterable[float]) -> float:
    values = list(values)
    return round(mean(values), 4) if values else 0.0


def fixed_horizon_paper_study(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Measure Lighter's executable top-of-book move after each experiment start.

    This is deliberately paper-only: it uses the initial ask/bid and a future
    opposite-side Lighter quote, so results remain before fill risk, impact,
    fees, funding, and execution latency.
    """
    lighter_quotes = sorted(
        (
            record
            for record in records
            if record.get("record_type") == "quote"
            and record.get("venue") == "Lighter.xyz"
            and isinstance(record.get("receive_monotonic_ns"), int)
        ),
        key=lambda record: record["receive_monotonic_ns"],
    )
    quote_times = [record["receive_monotonic_ns"] for record in lighter_quotes]
    starts = [
        record
        for record in records
        if record.get("record_type") == "lead_lag_event"
        and record.get("transition") == "STARTED"
        and isinstance(record.get("recorded_at_monotonic_ns"), int)
    ]
    results: Dict[int, List[float]] = defaultdict(list)
    usable_starts = 0

    for record in starts:
        event = record.get("event", {})
        context = record.get("execution_context", {}).get("lighter", {})
        direction = event.get("direction")
        entry_price = context.get("ask") if direction == "UPWARD_CATCHUP" else context.get("bid")
        if direction not in {"UPWARD_CATCHUP", "DOWNWARD_CATCHUP"} or not isinstance(entry_price, (int, float)) or entry_price <= 0:
            continue
        usable_starts += 1
        start_ns = record["recorded_at_monotonic_ns"]
        for horizon_ms in FORWARD_HORIZONS_MS:
            index = bisect_left(quote_times, start_ns + horizon_ms * 1_000_000)
            if index >= len(lighter_quotes):
                continue
            future_quote = lighter_quotes[index]
            exit_price = future_quote.get("bid") if direction == "UPWARD_CATCHUP" else future_quote.get("ask")
            if not isinstance(exit_price, (int, float)) or exit_price <= 0:
                continue
            pnl_per_btc = exit_price - entry_price if direction == "UPWARD_CATCHUP" else entry_price - exit_price
            results[horizon_ms].append(round(pnl_per_btc, 4))

    return {
        "event_starts": len(starts),
        "starts_with_executable_context": usable_starts,
        "paper_only": True,
        "cost_exclusions": "Excludes fill probability, market impact, execution latency, fees, funding, and liquidation costs.",
        "horizons_ms": {
            str(horizon): {
                "observations": len(values),
                "positive_rate_pct": round(100 * sum(value > 0 for value in values) / len(values), 2) if values else 0.0,
                "avg_pnl_per_btc_before_costs": average(values),
            }
            for horizon, values in ((horizon, results[horizon]) for horizon in FORWARD_HORIZONS_MS)
        },
    }


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    quote_records = [record for record in records if record.get("record_type") == "quote"]
    completed_events = [
        record["event"]
        for record in records
        if record.get("record_type") == "lead_lag_event"
        and record.get("transition") in {"SPREAD_CLOSED", "TIMEOUT"}
        and isinstance(record.get("event"), dict)
    ]
    closed_trades = [
        record["trade"]
        for record in records
        if record.get("record_type") == "paper_trade_close" and isinstance(record.get("trade"), dict)
    ]

    quotes_by_venue: Dict[str, Dict[str, Any]] = {}
    for venue, venue_records in defaultdict(list, {
        venue: [record for record in quote_records if record.get("venue") == venue]
        for venue in {record.get("venue") for record in quote_records}
    }).items():
        quotes_by_venue[venue] = {
            "quotes": len(venue_records),
            "exchange_timestamps_present": sum(record.get("exchange_timestamp_ms") is not None for record in venue_records),
            "source_sequences_present": sum(record.get("source_sequence") is not None for record in venue_records),
        }

    outcome_counts = Counter(event.get("resolution_type", "UNKNOWN") for event in completed_events)
    by_leader: Dict[str, Dict[str, Any]] = {}
    leaders = sorted({event.get("leading_exchange", "UNKNOWN") for event in completed_events})
    for leader in leaders:
        leader_events = [event for event in completed_events if event.get("leading_exchange") == leader]
        by_leader[leader] = {
            "events": len(leader_events),
            "lighter_catchups": sum(event.get("resolution_type") == "LIGHTER_CATCHUP" for event in leader_events),
            "leader_reversals": sum(event.get("resolution_type") == "LEADER_REVERSAL" for event in leader_events),
            "basis_shifts": sum(event.get("resolution_type") == "BASIS_SHIFT" for event in leader_events),
            "mixed_moves": sum(event.get("resolution_type") == "MIXED_MOVE" for event in leader_events),
            "timeouts": sum(event.get("resolution_type") == "UNRESOLVED_TIMEOUT" for event in leader_events),
            "avg_initial_lag_usd": average(event.get("initial_lag_usd", 0.0) for event in leader_events),
            "avg_spread_close_seconds": average(event.get("catchup_seconds", 0.0) for event in leader_events),
        }

    pnls = [float(trade.get("net_pnl", 0.0)) for trade in closed_trades]
    return {
        "records": len(records),
        "quote_records": len(quote_records),
        "quotes_by_venue": quotes_by_venue,
        "completed_spread_events": len(completed_events),
        "event_outcomes": dict(sorted(outcome_counts.items())),
        "events_by_leader": by_leader,
        "paper_trades": {
            "count": len(closed_trades),
            "wins": sum(pnl > 0 for pnl in pnls),
            "losses": sum(pnl <= 0 for pnl in pnls),
            "net_pnl_usd": round(sum(pnls), 4),
            "paper_only": True,
        },
        "fixed_horizon_executable_paper_study": fixed_horizon_paper_study(records),
        "interpretation": {
            "minimum_completed_events_for_review": 200,
            "enough_events_for_review": len(completed_events) >= 200,
            "warning": (
                "A closed spread is evidence only when resolution_type is LIGHTER_CATCHUP. "
                "Leader reversals, basis shifts, mixed moves, and paper PnL do not establish an executable edge."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize lead-lag experiment JSONL evidence.")
    parser.add_argument("logs", nargs="+", type=Path, help="One or more JSONL evidence files")
    args = parser.parse_args()
    missing = [str(path) for path in args.logs if not path.is_file()]
    if missing:
        parser.error("Missing log file(s): " + ", ".join(missing))
    print(json.dumps(summarize(read_records(args.logs)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
