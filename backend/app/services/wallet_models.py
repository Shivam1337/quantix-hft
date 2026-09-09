from dataclasses import dataclass


@dataclass(slots=True)
class WalletAsset:
    symbol: str
    total: float
    available: float | None = None
    locked: float | None = None


@dataclass(slots=True)
class WalletBalance:
    exchange_id: str
    exchange_name: str
    status: str
    total_usd: float | None = None
    available_usd: float | None = None
    assets: list[WalletAsset] | None = None
    message: str | None = None


class WalletValidationError(ValueError):
    pass
