import { memo } from 'preact/compat';
import { finite, money, number, quoteSize } from '../lib/format.js';

const VENUES = [
  { key: 'binance', id: 'card-binance', title: 'BINANCE FUTURES', color: 'var(--bn-color)', note: 'Global Depth' },
  { key: 'bybit', id: 'card-bybit', title: 'BYBIT LINEAR', color: 'var(--bybit-color)', note: 'Momentum' },
  { key: 'okx', id: 'card-okx', title: 'OKX PERP', color: 'var(--okx-color)', note: 'Asian Orderbook' },
  { key: 'hyperliquid', id: 'card-hl', title: 'HYPERLIQUID', color: 'var(--hl-color)', note: 'DEX Discovery' },
  { key: 'polymarket', id: 'card-poly', title: 'POLYMARKET', color: 'var(--poly-color)', lag: true },
  { key: 'lighter', id: 'card-lighter', title: 'LIGHTER.XYZ (ZK)', color: 'var(--lighter-color)', lag: true, lighter: true },
];

function price(value) {
  return finite(value) ? money(value, 1) : '$--';
}

function lagText(quote) {
  if (!quote || !finite(quote.lag_vs_leader)) return 'Lag vs Leader: --';
  const lag = Number(quote.lag_vs_leader);
  const sign = lag >= 0 ? '+' : '';
  return `Lag vs Leader: ${sign}${money(lag, 2)} (${sign}${quote.lag_bps || 0} bps)`;
}

function TickerStripContent({ market }) {
  return (
    <div class="ticker-strip">
      {VENUES.map((venue) => {
        const quote = market?.[venue.key] || {};
        return (
          <div class={`ticker-cell ${venue.lighter ? 'lighter-target' : ''}`} id={venue.id} key={venue.key}>
            <div class="ticker-cell-header">
              <span style={{ color: venue.color }}>● {venue.title}</span>
              <span class={venue.lighter ? 'zero-fee-pill' : 'mono ticker-status-text'} style={venue.lighter ? undefined : { color: venue.color }}>
                {quote.status || 'WAITING'}
              </span>
            </div>
            <div class="ticker-cell-price mono" style={{ color: venue.color }}>{price(quote.mid_price)}</div>
            <div class="ticker-cell-meta mono">
              <span>{venue.lag ? lagText(quote) : `Spread: ${money(quote.spread, 2)}`}</span>
              <span style={{ color: venue.color }}>{venue.lag ? `Spread: ${money(quote.spread, 2)}` : venue.note}</span>
            </div>
            <div class="ticker-bids-asks mono">
              <span>Bid <strong style={{ color: 'var(--green)' }}>{price(quote.best_bid)}</strong>{venue.lighter && <small> {quoteSize(quote.top_bid_size)}</small>}</span>
              <span>Ask <strong style={{ color: 'var(--red)' }}>{price(quote.best_ask)}</strong>{venue.lighter && <small> {quoteSize(quote.top_ask_size)}</small>}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export const TickerStrip = memo(TickerStripContent);
