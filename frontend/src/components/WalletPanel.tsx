import { useEffect, useState } from "react";
import { getWallet, importWallet, refreshWallet, removeWallet } from "../api";
import type { WalletExchangeBalance, WalletSnapshot } from "../types";

function formatUsd(value: number | null): string {
  return value === null ? "—" : `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatAsset(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 8 });
}

function statusClass(status: string): string {
  if (status === "connected") return "bg-emerald-400/10 text-emerald-300";
  if (status === "error") return "bg-rose-400/10 text-rose-300";
  if (status === "not_configured") return "bg-amber-400/10 text-amber-300";
  return "bg-slate-800 text-slate-400";
}

function BalanceCard({ balance }: { balance: WalletExchangeBalance }) {
  const assets = balance.assets.slice(0, 6);
  return (
    <article className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-white">{balance.exchange_name}</h3>
          <p className="mt-1 text-[11px] text-slate-500">{balance.exchange_id}</p>
        </div>
        <span className={`rounded-full px-2 py-1 text-[10px] font-semibold uppercase ${statusClass(balance.status)}`}>
          {balance.status.replaceAll("_", " ")}
        </span>
      </div>
      <div className="mt-5 grid grid-cols-2 gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-wider text-slate-500">Account value</p>
          <p className="mt-1 font-mono text-lg font-semibold text-white">{formatUsd(balance.total_usd)}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wider text-slate-500">Available</p>
          <p className="mt-1 font-mono text-lg font-semibold text-cyan">{formatUsd(balance.available_usd)}</p>
        </div>
      </div>
      {assets.length > 0 && (
        <div className="mt-4 border-t border-slate-800 pt-3">
          <p className="text-[10px] uppercase tracking-wider text-slate-500">Assets</p>
          <div className="mt-2 space-y-1">
            {assets.map((asset) => (
              <div key={asset.symbol} className="flex justify-between gap-3 text-xs">
                <span className="text-slate-400">{asset.symbol}</span>
                <span className="font-mono text-slate-200">{formatAsset(asset.total)}</span>
              </div>
            ))}
          </div>
          {balance.assets.length > assets.length && (
            <p className="mt-2 text-[10px] text-slate-500">+{balance.assets.length - assets.length} more assets</p>
          )}
        </div>
      )}
      {balance.message && <p className="mt-3 text-xs text-slate-500">{balance.message}</p>}
    </article>
  );
}

export function WalletPanel() {
  const [wallet, setWallet] = useState<WalletSnapshot | null>(null);
  const [privateKey, setPrivateKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const sync = async () => {
      try {
        let snapshot = await getWallet();
        if (snapshot.connected) snapshot = await refreshWallet();
        if (active) {
          setWallet(snapshot);
          setError("");
        }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Could not load wallet state");
      } finally {
        if (active) setLoading(false);
      }
    };
    void sync();
    const timer = window.setInterval(() => void sync(), 30_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const submit = async () => {
    if (!privateKey.trim()) return;
    setBusy(true);
    setError("");
    try {
      setWallet(await importWallet(privateKey.trim()));
      setPrivateKey("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not import wallet");
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    setBusy(true);
    setError("");
    try {
      setWallet(await refreshWallet());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not refresh balances");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setError("");
    try {
      setWallet(await removeWallet());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not remove wallet");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel col-span-12">
      <div className="section-heading">
        <div>
          <p className="eyebrow text-cyan">Read-only wallet</p>
          <h2>Wallet & Exchange Balances</h2>
        </div>
        <span className="count-badge uppercase">No orders</span>
      </div>
      <div className="space-y-5 p-5">
        <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-4 text-xs leading-5 text-amber-100">
          The private key stays in backend memory only and is never returned or persisted. This panel only derives the public address and reads balances; it does not sign or submit trades.
        </div>

        {loading ? (
          <div className="empty-state">Loading wallet state…</div>
        ) : wallet?.connected ? (
          <div className="flex flex-col gap-4 rounded-xl border border-cyan/20 bg-cyan/5 p-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="eyebrow text-cyan">Imported address</p>
              <p className="mt-2 break-all font-mono text-sm text-white">{wallet.address}</p>
              <p className="mt-2 text-xs text-slate-400">
                {wallet.refreshed_at ? `Last refreshed ${new Date(wallet.refreshed_at).toLocaleString()}` : "Balance refresh pending"}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <button className="button button-primary" onClick={() => void refresh()} disabled={busy}>Refresh balances</button>
              <button className="button button-danger" onClick={() => void remove()} disabled={busy}>Remove wallet</button>
            </div>
          </div>
        ) : (
          <form
            className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <label className="field-label">
              Ethereum private key
              <input
                className="field"
                type="password"
                value={privateKey}
                onChange={(event) => setPrivateKey(event.target.value)}
                placeholder="0x…"
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <button className="button button-primary" type="submit" disabled={busy || !privateKey.trim()}>
              {busy ? "Importing…" : "Import & read balances"}
            </button>
          </form>
        )}

        {error && <div className="alert">{error}</div>}
        {wallet?.message && <p className="text-xs text-slate-400">{wallet.message}</p>}
        {wallet?.connected && (
          <>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              {wallet.balances.map((balance) => <BalanceCard key={balance.exchange_id} balance={balance} />)}
            </div>
            <p className="text-xs text-slate-500">
              {wallet.live_trading_enabled
                ? "The server flag is enabled, but wallet order submission is not implemented by this read-only panel."
                : "Live trading is disabled on the server (LIVE_TRADING_ENABLED=false)."}
            </p>
          </>
        )}
      </div>
    </section>
  );
}
