import { memo } from 'preact/compat';
import { finite, money, number } from '../lib/format.js';

function StatCard({ label, value, sub, color }) {
  return (
    <div class="perf-card">
      <div class="stat-label">{label}</div>
      <div class="stat-val" style={{ color }}>{value}</div>
      <div class="stat-sub">{sub}</div>
    </div>
  );
}

function PerformanceStripContent({ performance, market }) {
  const data = performance || {};
  const real = data.is_real_mode === true;
  const ready = !real || data.account_data_available === true;
  const amount = (value, digits = 2) => ready ? money(value, digits) : '--';
  const configuredMargin = Number(data.configured_target_margin_usd) || 0;
  const targetMargin = Number(data.target_margin_usd) || 0;
  const capped = real && configuredMargin > targetMargin + 0.0001;
  const targetNotional = Number(data.target_notional_usd) || 0;
  const lighterPrice = Number(market?.lighter?.mid_price) || 0;
  const targetBtc = ready && lighterPrice && targetNotional ? (targetNotional / lighterPrice).toFixed(4) : '--';
  const netPnl = Number(data.net_pnl) || 0;
  const returnOnMargin = Number(data.return_on_margin_pct) || 0;
  const cards = [
    {
      label: real ? 'Real Account Equity' : 'Account Equity', value: amount(data.account_equity_usd), color: 'var(--cyan)',
      sub: real ? (ready ? `Lighter collateral: ${money(data.account_collateral_usd)} · Free: ${money(data.free_margin_usd)}` : 'Awaiting verified Lighter account snapshot…') : `Base: ${money(data.account_base_balance_usd, 0)} · Free: ${money(data.free_margin_usd)}`,
    },
    {
      label: real ? 'Real Margin Target' : `Margin @ ${data.leverage ?? 50}x Leverage`, value: `${amount(targetMargin)} (${capped ? `${money(configuredMargin)} configured · free-margin cap` : `${number(data.target_margin_fraction_pct ?? 0, 1)}% target · ${number(data.margin_utilization_pct ?? 0, 1)}% used`})`, color: 'var(--lighter-color)',
      sub: real ? `${data.leverage ?? 50}x configured · Exchange margin used: ${money(data.margin_used_usd)}` : `${data.leverage ?? 50}x Lighter Leverage`,
    },
    {
      label: real ? 'Real Target Notional' : 'Target Notional', value: amount(targetNotional, 0), color: '#cbd5e1',
      sub: real ? `BTC target ~${targetBtc} · Position: ${money(data.account_position_notional_usd, 0)}` : `Dynamic ~${targetBtc} BTC`,
    },
    {
      label: real ? 'Net PnL (Confirmed Real)' : 'Net PnL (Lighter)', value: `${netPnl >= 0 ? '+' : ''}${money(netPnl)}`, color: netPnl > 0 ? 'var(--green)' : netPnl < 0 ? 'var(--red)' : 'var(--text)',
      sub: real ? `Confirmed strategy PnL · RoM: ${returnOnMargin >= 0 ? '+' : ''}${number(returnOnMargin, 1)}%` : `RoM: ${returnOnMargin >= 0 ? '+' : ''}${number(returnOnMargin, 1)}% · 0% Fees`,
    },
    {
      label: real ? 'Real Win Rate' : 'Win Rate', value: `${data.win_rate ?? 0}%`, color: 'var(--cyan)',
      sub: `${data.total_trades ?? 0} ${real ? 'Confirmed Real ' : ''}Trades (${data.wins ?? 0}W / ${data.losses ?? 0}L)`,
    },
    {
      label: real ? 'Fees Saved (Real)' : 'Fees Saved vs Poly', value: money(data.fees_saved_vs_poly), color: 'var(--green)',
      sub: real ? `Est. vs ${number(data.fees_saved_rate_pct, 2)}% round-trip alternative` : 'Avoided $64/BTC hurdle',
    },
  ];
  return <div class="perf-strip mono">{cards.map((card) => <StatCard {...card} key={card.label} />)}</div>;
}

export const PerformanceStrip = memo(PerformanceStripContent);
