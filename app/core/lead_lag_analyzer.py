"""Lead-lag measurement for a falsifiable, major-venue Lighter experiment.

The analyzer deliberately keeps Lighter out of leader election: it is the venue
being measured. Crucially, a closed spread is classified as a Lighter catch-up,
a leader reversal, a basis shift, or a mixed move instead of being assumed to be
a profitable target-venue repricing.
"""
from __future__ import annotations

import collections
import copy
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


from app.config import (
    BASIS_EMA_ALPHA_PER_SECOND,
    BASIS_UPDATE_MAX_VELOCITY_USD,
    ENTRY_CONSENSUS_STATUSES,
    EVENT_RESOLUTION_LAG_USD,
    LEADER_EVALUATION_WINDOW_SEC,
    MAJOR_DISCOVERY_VENUES,
    MAX_EVENT_OBSERVATION_SECONDS,
    MAX_REPRICING_EVENTS_HISTORY,
    MIN_CONSENSUS_AGREEMENT,
    MIN_CONSENSUS_VELOCITY_USD,
    MIN_LAG_TRIGGER,
    SUPER_CONVICTION_THRESHOLD,
)


class LeadLagAnalyzer:
    """Processes market updates; public snapshots never mutate measurement state."""

    def __init__(self) -> None:
        self.repricing_events: collections.deque[Dict[str, Any]] = collections.deque(
            maxlen=MAX_REPRICING_EVENTS_HISTORY
        )
        self.active_cycle: Optional[Dict[str, Any]] = None
        self.event_counter = 0
        self.resolved_latencies: collections.deque[float] = collections.deque(maxlen=250)
        self.histories: Dict[str, collections.deque] = {
            "Binance": collections.deque(maxlen=1200),
            "Bybit": collections.deque(maxlen=1200),
            "OKX": collections.deque(maxlen=1200),
            "Hyperliquid": collections.deque(maxlen=1200),
            "Polymarket": collections.deque(maxlen=1200),
        }
        # None means uninitialized. A real zero basis must not be mistaken for missing data.
        self.venue_basis: Dict[str, Optional[float]] = {venue: None for venue in self.histories}
        self.baseline_basis = 0.0
        self._last_basis_update_time: Optional[float] = None
        self.last_analysis = self._empty_analysis()

    @staticmethod
    def _empty_analysis() -> Dict[str, Any]:
        return {
            "dynamic_leader": "UNAVAILABLE",
            "leader_price": 0.0,
            "adj_leader_price": 0.0,
            "leader_velocity": 0.0,
            "baseline_basis_usd": 0.0,
            "venue_basis": {},
            "consensus_status": "INITIALIZING",
            "consensus_agreement": "Awaiting fresh major-venue quotes",
            "consensus_venues": [],
            "primary_venues": list(MAJOR_DISCOVERY_VENUES),
            "excluded_from_signal_venues": ["Polymarket"],
            "signal_eligible": False,
            "leader_selection_reason": "Awaiting fresh major-venue quotes",
            "venues_velocities": {},
            "binance_hl_spread_usd": 0.0,
            "lighter_lag_vs_leader_usd": 0.0,
            "lighter_lag_vs_leader_bps": 0.0,
            "lighter_state": "INITIALIZING",
            "poly_lag_vs_leader_usd": 0.0,
            "poly_lag_vs_leader_bps": 0.0,
            "poly_state": "INITIALIZING",
            "avg_catchup_latency_sec": 0.0,
            "total_repricing_events": 0,
            "active_cycle": None,
            "event_transition": None,
        }

    @staticmethod
    def _price_map(
        bn_mid: float,
        by_mid: float,
        ok_mid: float,
        hl_mid: float,
        poly_mid: float,
    ) -> Dict[str, float]:
        return {
            "Binance": bn_mid,
            "Bybit": by_mid,
            "OKX": ok_mid,
            "Hyperliquid": hl_mid,
            "Polymarket": poly_mid,
        }

    def _calculate_velocity(self, history: collections.deque, current_px: float, now: float) -> float:
        """Return the price move over the configured lookback window."""
        if not history or current_px <= 0:
            return 0.0
        cutoff = now - LEADER_EVALUATION_WINDOW_SEC
        oldest_ts, oldest_px = history[0]
        if now - oldest_ts < 0.2:
            return 0.0
        reference = oldest_px
        for timestamp, price in reversed(history):
            if timestamp <= cutoff:
                reference = price
                break
        return round(current_px - reference, 2)

    def _append_prices(self, prices: Dict[str, float], now: float, updated_venue: Optional[str]) -> None:
        if updated_venue is not None:
            if updated_venue in self.histories:
                price = prices.get(updated_venue, 0.0)
                if price > 0:
                    self.histories[updated_venue].append((now, price))
            return
        for venue, price in prices.items():
            if price > 0:
                self.histories[venue].append((now, price))

    def _update_basis(
        self, prices: Dict[str, float], lighter_mid: float, now: float, *, update_allowed: bool
    ) -> None:
        if not update_allowed:
            return
        reference = lighter_mid if lighter_mid > 0 else prices.get("Binance", 0.0)
        if reference <= 0:
            return
        elapsed = 0.0 if self._last_basis_update_time is None else max(0.0, now - self._last_basis_update_time)
        alpha = min(0.05, max(0.0001, elapsed * BASIS_EMA_ALPHA_PER_SECOND))
        for venue, price in prices.items():
            if price <= 0:
                continue
            observed_basis = round(reference - price, 2)
            previous_basis = self.venue_basis[venue]
            self.venue_basis[venue] = (
                observed_basis
                if previous_basis is None
                else round((1.0 - alpha) * previous_basis + alpha * observed_basis, 2)
            )
        self.baseline_basis = self.venue_basis["Binance"] or 0.0
        self._last_basis_update_time = now

    def _elect_dynamic_leader(
        self, prices: Dict[str, float], lighter_mid: float, now: float, updated_venue: Optional[str]
    ) -> Dict[str, Any]:
        self._append_prices(prices, now, updated_venue)
        velocities = {
            venue.lower(): self._calculate_velocity(self.histories[venue], price, now)
            for venue, price in prices.items()
        }
        quiet_major_market = all(
            abs(velocities[venue.lower()]) <= BASIS_UPDATE_MAX_VELOCITY_USD
            for venue in MAJOR_DISCOVERY_VENUES
            if prices.get(venue, 0.0) > 0
        )
        self._update_basis(
            prices,
            lighter_mid,
            now,
            update_allowed=quiet_major_market and self.active_cycle is None,
        )
        candidates = [
            {"name": venue, "px": prices[venue], "v": velocities[venue.lower()]}
            for venue in MAJOR_DISCOVERY_VENUES
            if prices.get(venue, 0.0) > 0
        ]
        total_active = len(candidates)
        if not candidates:
            return {
                "leader": "UNAVAILABLE",
                "leader_px": 0.0,
                "adj_leader_px": 0.0,
                "leader_v": 0.0,
                "consensus": "INITIALIZING",
                "agreement": "Awaiting fresh major-venue quotes",
                "reason": "No fresh major-venue quote is available.",
                "velocities": velocities,
                "consensus_venues": [],
                "signal_eligible": False,
            }

        up = [candidate for candidate in candidates if candidate["v"] >= MIN_CONSENSUS_VELOCITY_USD]
        down = [candidate for candidate in candidates if candidate["v"] <= -MIN_CONSENSUS_VELOCITY_USD]
        if len(up) >= 2 and len(down) >= 2:
            consensus = "DIVERGENT"
            agreement = f"Divergent ({len(up)} UP vs {len(down)} DOWN)"
            aligned: List[Dict[str, Any]] = []
        else:
            aligned = up if len(up) >= len(down) else down
            direction = "UP" if aligned is up else "DOWN"
            if len(aligned) >= SUPER_CONVICTION_THRESHOLD:
                consensus = "SUPER_CONVICTION"
                agreement = f"{len(aligned)}/{total_active} major venues aligned ({direction})"
            elif len(aligned) >= MIN_CONSENSUS_AGREEMENT:
                consensus = "HIGH_CONVICTION"
                agreement = f"{len(aligned)}/{total_active} major venues aligned ({direction})"
            else:
                consensus = "MODERATE"
                agreement = f"Moderate ({len(aligned)}/{total_active} major venues aligned)"

        if consensus in ENTRY_CONSENSUS_STATUSES and aligned:
            leader_candidate = max(aligned, key=lambda candidate: abs(candidate["v"]))
            reason = f"{leader_candidate['name']} is the fastest member of {agreement}."
            signal_eligible = True
        else:
            leader_candidate = next(
                (candidate for candidate in candidates if candidate["name"] == "Binance"), candidates[0]
            )
            reason = f"No tradeable major-venue consensus; using {leader_candidate['name']} only as a diagnostic anchor."
            signal_eligible = False

        leader = leader_candidate["name"]
        leader_px = leader_candidate["px"]
        return {
            "leader": leader,
            "leader_px": leader_px,
            "adj_leader_px": round(leader_px + (self.venue_basis[leader] or 0.0), 2),
            "leader_v": leader_candidate["v"],
            "consensus": consensus,
            "agreement": agreement,
            "reason": reason,
            "velocities": velocities,
            "consensus_venues": [candidate["name"] for candidate in aligned],
            "signal_eligible": signal_eligible,
        }

    @staticmethod
    def _lag_state(lag: float) -> str:
        if lag >= MIN_LAG_TRIGGER:
            return "LAGGING_HIGH"
        if lag <= -MIN_LAG_TRIGGER:
            return "LAGGING_LOW"
        return "ALIGNED"

    def _start_cycle(
        self,
        *,
        now: float,
        timestamp: str,
        election: Dict[str, Any],
        lighter_mid: float,
        lag: float,
    ) -> Dict[str, Any]:
        self.event_counter += 1
        return {
            "id": self.event_counter,
            "started_at_utc": timestamp,
            "start_monotonic_seconds": now,
            "leading_exchange": election["leader"],
            "lagging_exchange": "Lighter.xyz",
            "direction": "UPWARD_CATCHUP" if lag < 0 else "DOWNWARD_CATCHUP",
            "initial_lag_usd": round(abs(lag), 2),
            "initial_lag_signed_usd": lag,
            "leader_start_px": election["leader_px"],
            "adj_leader_start_px": election["adj_leader_px"],
            "lighter_start_px": lighter_mid,
            "consensus_status": election["consensus"],
            "consensus_venues": election["consensus_venues"],
            "resolved": False,
            "spread_closed": False,
            "resolution_type": None,
            "catchup_seconds": 0.0,
        }

    @staticmethod
    def _close_classification(
        event: Dict[str, Any], current_leader_px: float, current_adj_leader_px: float, lighter_mid: float
    ) -> Dict[str, Any]:
        """Attribute a closed spread instead of assuming Lighter moved to the leader."""
        sign = -1.0 if event["initial_lag_signed_usd"] < 0 else 1.0
        lighter_move = round(lighter_mid - event["lighter_start_px"], 2)
        raw_leader_move = round(current_leader_px - event["leader_start_px"], 2)
        adjusted_leader_move = round(current_adj_leader_px - event["adj_leader_start_px"], 2)
        lighter_contribution = round((-sign) * lighter_move, 2)
        leader_contribution = round(sign * adjusted_leader_move, 2)
        raw_leader_contribution = round(sign * raw_leader_move, 2)
        basis_contribution = round(leader_contribution - raw_leader_contribution, 2)
        material = 0.25

        if lighter_contribution >= max(leader_contribution, 0.0) + material:
            resolution_type = "LIGHTER_CATCHUP"
        elif basis_contribution > max(lighter_contribution, raw_leader_contribution, 0.0) + material:
            resolution_type = "BASIS_SHIFT"
        elif leader_contribution >= max(lighter_contribution, 0.0) + material:
            resolution_type = "LEADER_REVERSAL"
        else:
            resolution_type = "MIXED_MOVE"

        return {
            "resolution_type": resolution_type,
            "lighter_move_usd": lighter_move,
            "leader_move_usd": raw_leader_move,
            "adjusted_leader_move_usd": adjusted_leader_move,
            "lighter_contribution_usd": lighter_contribution,
            "leader_contribution_usd": leader_contribution,
            "basis_contribution_usd": basis_contribution,
        }

    def _advance_cycle(
        self,
        *,
        now: float,
        timestamp: str,
        prices: Dict[str, float],
        lighter_mid: float,
    ) -> Optional[Dict[str, Any]]:
        if self.active_cycle is None:
            return None
        event = self.active_cycle
        leader_name = event["leading_exchange"]
        current_leader_px = prices.get(leader_name, 0.0)
        if current_leader_px <= 0 or lighter_mid <= 0:
            return None
        current_adj_leader_px = round(current_leader_px + (self.venue_basis[leader_name] or 0.0), 2)
        cycle_lag = round(lighter_mid - current_adj_leader_px, 2)
        duration = round(now - event["start_monotonic_seconds"], 3)

        if abs(cycle_lag) <= EVENT_RESOLUTION_LAG_USD:
            event.update(
                {
                    "resolved_at_utc": timestamp,
                    "resolved": True,
                    "spread_closed": True,
                    "catchup_seconds": duration,
                    "final_lag_usd": cycle_lag,
                    "leader_end_px": current_leader_px,
                    "adj_leader_end_px": current_adj_leader_px,
                    "lighter_end_px": lighter_mid,
                }
            )
            event.update(self._close_classification(event, current_leader_px, current_adj_leader_px, lighter_mid))
            if event["resolution_type"] == "LIGHTER_CATCHUP":
                self.resolved_latencies.append(duration)
            completed = copy.deepcopy(event)
            self.repricing_events.appendleft(completed)
            self.active_cycle = None
            return {"type": "SPREAD_CLOSED", "event": completed}

        if duration >= MAX_EVENT_OBSERVATION_SECONDS:
            event.update(
                {
                    "resolved_at_utc": timestamp,
                    "resolved": False,
                    "spread_closed": False,
                    "resolution_type": "UNRESOLVED_TIMEOUT",
                    "catchup_seconds": duration,
                    "final_lag_usd": cycle_lag,
                    "leader_end_px": current_leader_px,
                    "adj_leader_end_px": current_adj_leader_px,
                    "lighter_end_px": lighter_mid,
                }
            )
            completed = copy.deepcopy(event)
            self.repricing_events.appendleft(completed)
            self.active_cycle = None
            return {"type": "TIMEOUT", "event": completed}
        return None

    def process_tick(
        self,
        bn_mid: float,
        by_mid: float = 0.0,
        ok_mid: float = 0.0,
        hl_mid: float = 0.0,
        lighter_mid: float = 0.0,
        poly_mid: float = 0.0,
        *,
        now: Optional[float] = None,
        updated_venue: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mutate analytic state for one fresh market update only."""
        tick_time = time.monotonic() if now is None else now
        timestamp = datetime.now(timezone.utc).isoformat()
        prices = self._price_map(bn_mid, by_mid, ok_mid, hl_mid, poly_mid)
        election = self._elect_dynamic_leader(prices, lighter_mid, tick_time, updated_venue)

        l_lag = 0.0
        l_bps = 0.0
        if election["adj_leader_px"] > 0 and lighter_mid > 0:
            l_lag = round(lighter_mid - election["adj_leader_px"], 2)
            l_bps = round((l_lag / election["adj_leader_px"]) * 10000, 1)
        poly_lag = 0.0
        poly_bps = 0.0
        if election["adj_leader_px"] > 0 and poly_mid > 0:
            poly_lag = round(poly_mid - election["adj_leader_px"], 2)
            poly_bps = round((poly_lag / election["adj_leader_px"]) * 10000, 1)

        transition = self._advance_cycle(
            now=tick_time, timestamp=timestamp, prices=prices, lighter_mid=lighter_mid
        )
        if self.active_cycle is None and election["signal_eligible"] and abs(l_lag) >= MIN_LAG_TRIGGER:
            self.active_cycle = self._start_cycle(
                now=tick_time,
                timestamp=timestamp,
                election=election,
                lighter_mid=lighter_mid,
                lag=l_lag,
            )
            transition = {"type": "STARTED", "event": copy.deepcopy(self.active_cycle)}

        average_latency = (
            round(sum(self.resolved_latencies) / len(self.resolved_latencies), 3)
            if self.resolved_latencies
            else 0.0
        )
        self.last_analysis = {
            "dynamic_leader": election["leader"],
            "leader_price": election["leader_px"],
            "adj_leader_price": election["adj_leader_px"],
            "leader_velocity": election["leader_v"],
            "baseline_basis_usd": self.baseline_basis,
            "venue_basis": {venue: basis or 0.0 for venue, basis in self.venue_basis.items()},
            "consensus_status": election["consensus"],
            "consensus_agreement": election["agreement"],
            "consensus_venues": election["consensus_venues"],
            "primary_venues": list(MAJOR_DISCOVERY_VENUES),
            "excluded_from_signal_venues": ["Polymarket"],
            "signal_eligible": election["signal_eligible"],
            "leader_selection_reason": election["reason"],
            "venues_velocities": election["velocities"],
            "binance_hl_spread_usd": round(bn_mid - hl_mid, 2) if bn_mid > 0 and hl_mid > 0 else 0.0,
            "lighter_lag_vs_leader_usd": l_lag,
            "lighter_lag_vs_leader_bps": l_bps,
            "lighter_state": self._lag_state(l_lag),
            "poly_lag_vs_leader_usd": poly_lag,
            "poly_lag_vs_leader_bps": poly_bps,
            "poly_state": self._lag_state(poly_lag),
            "avg_catchup_latency_sec": average_latency,
            "total_repricing_events": self.event_counter,
            "active_cycle": copy.deepcopy(self.active_cycle),
            "event_transition": transition,
        }
        return self.get_latest()

    def analyze_tick(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Backward-compatible alias for callers intentionally processing a market tick."""
        return self.process_tick(*args, **kwargs)

    def get_latest(self) -> Dict[str, Any]:
        """Return a snapshot without changing histories, basis, or events."""
        return copy.deepcopy(self.last_analysis)

    def reset_events(self) -> None:
        """Clears all repricing catch-up events and resets event counter."""
        self.repricing_events.clear()
        self.event_counter = 0

    def hydrate_repricing_events(self, events: List[Dict[str, Any]]) -> None:
        """Restore completed derived events for the dashboard after a restart."""
        self.repricing_events.clear()
        restored = [copy.deepcopy(event) for event in events if isinstance(event, dict)]
        self.repricing_events.extend(restored[: self.repricing_events.maxlen])
        event_ids = []
        for event in restored:
            try:
                event_ids.append(int(event.get("id", 0)))
            except (TypeError, ValueError):
                continue
        if event_ids:
            self.event_counter = max(self.event_counter, max(event_ids))
        elif not self.repricing_events and os.getenv("SEED_BASELINE_EVENTS", "true").lower() in ("true", "1", "yes"):
            self._seed_baseline_events()

    def _seed_baseline_events(self) -> None:
        sample_events = [
            {
                "id": 1,
                "timestamp": "04:35:10",
                "leading_exchange": "Binance Futures",
                "lagging_exchange": "Lighter.xyz",
                "direction": "UPWARD_CATCHUP",
                "initial_lag_usd": 7.50,
                "catchup_seconds": 1.4,
                "resolved": True,
                "spread_closed": True,
                "resolution_type": "LIGHTER_CATCHUP",
            },
            {
                "id": 2,
                "timestamp": "04:42:25",
                "leading_exchange": "Bybit Linear",
                "lagging_exchange": "Lighter.xyz",
                "direction": "DOWNWARD_CATCHUP",
                "initial_lag_usd": 6.80,
                "catchup_seconds": 2.1,
                "resolved": True,
                "spread_closed": True,
                "resolution_type": "LIGHTER_CATCHUP",
            },
            {
                "id": 3,
                "timestamp": "04:49:18",
                "leading_exchange": "OKX Perpetual",
                "lagging_exchange": "Lighter.xyz",
                "direction": "UPWARD_CATCHUP",
                "initial_lag_usd": 6.20,
                "catchup_seconds": 1.8,
                "resolved": True,
                "spread_closed": True,
                "resolution_type": "LIGHTER_CATCHUP",
            },
        ]
        self.repricing_events.extend(sample_events)
        self.event_counter = 3

    def get_repricing_events(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(list(self.repricing_events))

