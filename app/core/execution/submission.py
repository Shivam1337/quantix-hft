"""Validated acknowledgement receipts for Lighter transaction submission."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional, Tuple


SUCCESS_RESPONSE_CODES = frozenset({0, 200})


@dataclass(frozen=True)
class LighterSubmissionReceipt:
    """The only result that may advance an order to reconciliation.

    ``tx_hash`` alone is not an acknowledgement: Lighter's send-transaction
    response also carries an application response code.  The iterator keeps
    compatibility with the older ``success, tx_hash, error = ...`` call sites.
    """

    success: bool
    tx_hash: Optional[str]
    error: Optional[str]
    response_code: Optional[int]
    response_message: Optional[str]
    predicted_execution_time_ms: Optional[int]
    volume_quota_remaining: Optional[int]
    received_at: float
    uncertain: bool = False

    def __iter__(self) -> Iterator[object]:
        yield self.success
        yield self.tx_hash
        yield self.error

    @property
    def response_metadata(self) -> dict[str, Optional[int | str]]:
        return {
            "response_code": self.response_code,
            "response_message": self.response_message,
            "predicted_execution_time_ms": self.predicted_execution_time_ms,
            "volume_quota_remaining": self.volume_quota_remaining,
        }

    @classmethod
    def from_response(cls, response: Any, error: Any = None) -> "LighterSubmissionReceipt":
        """Accept only a successful Lighter response with a usable hash."""
        now = time.time()
        if error:
            return cls(False, None, str(error), None, None, None, None, now)

        code = _as_int(_field(response, "code"))
        message = _as_text(_field(response, "message"))
        tx_hash = _as_text(_field(response, "tx_hash"))
        predicted = _as_int(_field(response, "predicted_execution_time_ms"))
        quota = _as_int(_field(response, "volume_quota_remaining"))
        if code not in SUCCESS_RESPONSE_CODES:
            detail = message or "Lighter returned no successful response code."
            return cls(
                False, tx_hash, f"Lighter transaction rejected (code={code}): {detail}",
                code, message, predicted, quota, now, uncertain=bool(tx_hash),
            )
        if not tx_hash:
            return cls(
                False, None, "Lighter accepted no transaction hash for this order.",
                code, message, predicted, quota, now, uncertain=True,
            )
        return cls(True, tx_hash, None, code, message, predicted, quota, now)

    @classmethod
    def failure(cls, error: Any, *, uncertain: bool = False) -> "LighterSubmissionReceipt":
        return cls(False, None, str(error), None, None, None, None, time.time(), uncertain)


def coerce_submission_receipt(value: Any) -> LighterSubmissionReceipt:
    """Adapt existing test doubles and integrations while callers migrate."""
    if isinstance(value, LighterSubmissionReceipt):
        return value
    try:
        success, tx_hash, error = value
    except (TypeError, ValueError):
        return LighterSubmissionReceipt.failure("Malformed Lighter submission result.", uncertain=True)
    if success:
        # Old adapters cannot prove the API response code.  Keep tests and
        # non-SDK integrations working, but production uses ``from_response``.
        normalized_hash = _as_text(tx_hash)
        if not normalized_hash:
            return LighterSubmissionReceipt.failure("Successful submission adapter returned no transaction hash.", uncertain=True)
        return LighterSubmissionReceipt(True, normalized_hash, None, 200, None, None, None, time.time())
    return LighterSubmissionReceipt.failure(error or "Lighter submission failed.")


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
