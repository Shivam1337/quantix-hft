from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class FundingSnapshot(Base):
    __tablename__ = "funding_snapshots"
    __table_args__ = (
        UniqueConstraint("venue", "symbol", "funding_cycle_at", name="uq_funding_snapshot_venue_symbol_cycle"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    funding_rate: Mapped[float] = mapped_column(Float)
    funding_rate_native: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_interval_hours: Mapped[float] = mapped_column(Float, default=1.0)
    funding_cycle_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    mark_price: Mapped[float] = mapped_column(Float)
    open_interest: Mapped[float] = mapped_column(Float)
    bid: Mapped[float] = mapped_column(Float)
    ask: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(String(160), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    long_venue: Mapped[str] = mapped_column(String(32))
    short_venue: Mapped[str] = mapped_column(String(32))
    size_usd: Mapped[float] = mapped_column(Float)
    long_entry_price: Mapped[float] = mapped_column(Float)
    short_entry_price: Mapped[float] = mapped_column(Float)
    entry_basis_bps: Mapped[float] = mapped_column(Float)
    current_long_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_short_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_basis_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_pnl_usd: Mapped[float] = mapped_column(Float, default=0)
    long_funding_pnl_usd: Mapped[float] = mapped_column(Float, default=0)
    short_funding_pnl_usd: Mapped[float] = mapped_column(Float, default=0)
    last_funding_cycle: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    basis_pnl_usd: Mapped[float] = mapped_column(Float, default=0)
    entry_fee_usd: Mapped[float] = mapped_column(Float, default=0)
    exit_fee_usd: Mapped[float] = mapped_column(Float, default=0)
    fees_usd: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    negative_hours: Mapped[int] = mapped_column(Integer, default=0)
    last_negative_hour: Mapped[str | None] = mapped_column(String(32), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    leg_size_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    venue: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(12))
    order_type: Mapped[str] = mapped_column(String(24))
    size_usd: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24))
    client_order_id: Mapped[str] = mapped_column(String(96))
    phase: Mapped[str] = mapped_column(String(12), default="open")
    fee_bps: Mapped[float] = mapped_column(Float, default=0)
    fee_usd: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    min_apr: Mapped[float] = mapped_column(Float, default=10)
    min_open_interest: Mapped[float] = mapped_column(Float, default=100_000)
    basis_threshold_bps: Mapped[float] = mapped_column(Float, default=75)
    auto_unwind: Mapped[bool] = mapped_column(default=True)
    alert_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(16))
    event: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SimulationAccount(Base):
    __tablename__ = "simulation_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    initial_balance: Mapped[float] = mapped_column(Float, default=10_000.0)
    current_balance: Mapped[float] = mapped_column(Float, default=10_000.0)
    allocated_balance: Mapped[float] = mapped_column(Float, default=0.0)
    total_realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FundingPayment(Base):
    __tablename__ = "funding_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[str] = mapped_column(String(36), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    long_venue: Mapped[str] = mapped_column(String(32))
    short_venue: Mapped[str] = mapped_column(String(32))
    long_rate: Mapped[float] = mapped_column(Float)
    short_rate: Mapped[float] = mapped_column(Float)
    long_payment_usd: Mapped[float] = mapped_column(Float)
    short_payment_usd: Mapped[float] = mapped_column(Float)
    net_payment_usd: Mapped[float] = mapped_column(Float)
    cycle_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

