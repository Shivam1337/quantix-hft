import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

VENUES = ["hyperliquid", "aevo", "lighter"]


class TelemetryService:
    def __init__(self, max_minutes: int = 60):
        self.max_minutes = max_minutes
        self._totals: dict[str, int] = {v: 0 for v in VENUES}
        self._minute_counts: dict[int, dict[str, int]] = defaultdict(lambda: {v: 0 for v in VENUES})
        self._rest_totals: dict[str, int] = {v: 0 for v in VENUES}
        self._rest_minute_counts: dict[int, dict[str, int]] = defaultdict(
            lambda: {v: 0 for v in VENUES}
        )
        self._rest_endpoints: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    def record_message(self, venue: str, count: int = 1) -> None:
        v = venue.lower()
        if v not in self._totals:
            self._totals[v] = 0
        self._totals[v] += count

        now_min = int(time.time() // 60) * 60
        self._minute_counts[now_min][v] += count
        self._prune_old_minutes(now_min)

    def record_rest_call(
        self, venue: str, method: str, endpoint: str, status_code: int = 200
    ) -> None:
        v = venue.lower()
        if v not in self._rest_totals:
            self._rest_totals[v] = 0
        self._rest_totals[v] += 1

        now_min = int(time.time() // 60) * 60
        self._rest_minute_counts[now_min][v] += 1

        m = method.upper()
        ep_key = f"{m} {endpoint}"
        ep_dict = self._rest_endpoints[v]
        if ep_key not in ep_dict:
            ep_dict[ep_key] = {
                "method": m,
                "endpoint": endpoint,
                "calls_total": 0,
                "last_called_at": None,
                "last_status": status_code,
            }
        ep_dict[ep_key]["calls_total"] += 1
        ep_dict[ep_key]["last_called_at"] = datetime.now(timezone.utc).isoformat()
        ep_dict[ep_key]["last_status"] = status_code

        self._prune_old_minutes(now_min)

    def _prune_old_minutes(self, current_min: int) -> None:
        cutoff = current_min - (self.max_minutes * 60)
        old_keys = [k for k in self._minute_counts if k < cutoff]
        for k in old_keys:
            del self._minute_counts[k]
        old_rest_keys = [k for k in self._rest_minute_counts if k < cutoff]
        for k in old_rest_keys:
            del self._rest_minute_counts[k]

    def get_throughput(self) -> dict[str, Any]:
        now = time.time()
        now_min = int(now // 60) * 60
        elapsed_sec = max(1.0, now - now_min)
        all_venues = sorted(self._totals.keys())
        history: list[dict[str, Any]] = []
        rest_history: list[dict[str, Any]] = []

        # Completed minutes
        for i in range(self.max_minutes - 1, 0, -1):
            m_ts = now_min - (i * 60)
            iso_ts = datetime.fromtimestamp(m_ts, tz=timezone.utc).isoformat()

            counts = {v: self._minute_counts[m_ts].get(v, 0) for v in all_venues}
            history.append({
                "timestamp": iso_ts, "minute": m_ts, "counts": counts, "total": sum(counts.values())
            })

            r_counts = {v: self._rest_minute_counts[m_ts].get(v, 0) for v in all_venues}
            rest_history.append(
                {
                    "timestamp": iso_ts,
                    "minute": m_ts,
                    "counts": r_counts,
                    "total": sum(r_counts.values()),
                }
            )

        # In-progress minute (now_min)
        prev_min_ts = now_min - 60
        current_rates = {}
        rest_current_rates = {}
        for v in all_venues:
            raw_count = self._minute_counts[now_min].get(v, 0)
            raw_rest = self._rest_minute_counts[now_min].get(v, 0)
            if elapsed_sec >= 10:
                current_rates[v] = int(round((raw_count / elapsed_sec) * 60))
                rest_current_rates[v] = int(round((raw_rest / elapsed_sec) * 60))
            else:
                p_rate = self._minute_counts[prev_min_ts].get(v, 0)
                current_rates[v] = p_rate if p_rate > 0 else raw_count
                p_rest = self._rest_minute_counts[prev_min_ts].get(v, 0)
                rest_current_rates[v] = p_rest if p_rest > 0 else raw_rest

        iso_now = datetime.fromtimestamp(now_min, tz=timezone.utc).isoformat()
        history.append(
            {
                "timestamp": iso_now,
                "minute": now_min,
                "counts": current_rates,
                "total": sum(current_rates.values()),
            }
        )
        rest_history.append({
            "timestamp": iso_now,
            "minute": now_min,
            "counts": rest_current_rates,
            "total": sum(rest_current_rates.values()),
        })

        endpoints_by_venue = {
            v: sorted(
                list(self._rest_endpoints.get(v, {}).values()),
                key=lambda x: x["calls_total"],
                reverse=True,
            )
            for v in all_venues
        }

        return {
            "venues": all_venues,
            "current_rates": current_rates,
            "total_processed": dict(self._totals),
            "history": history,
            "rest_current_rates": rest_current_rates,
            "rest_total_processed": dict(self._rest_totals),
            "rest_history": rest_history,
            "rest_endpoints": endpoints_by_venue,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# Global singleton
telemetry = TelemetryService()
