import type {
  ExchangeSummary,
  FundingPayment,
  FundingSettlement,
  Opportunity,
  Position,
  Settings,
  Simulation,
  SimulationAccount,
  SystemMetrics,
  ThroughputSummary,
  TradeLog,
} from "./types";

const base = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const getOpportunities = (query = "") =>
  request<Opportunity[]>(`/opportunities${query ? `?${query}` : ""}`);
export const getPositions = () => request<Position[]>("/positions?active_only=true");
export const getSettings = () => request<Settings>("/settings");
export const getLogs = () => request<TradeLog[]>("/logs?limit=50");
export const getFundingPayments = (limit = 100) => request<FundingPayment[]>(`/funding-payments?limit=${limit}`);
export const getSystemMetrics = () => request<SystemMetrics>("/system/metrics");
export const getExchanges = () => request<ExchangeSummary[]>("/exchanges");
export const getThroughput = () => request<ThroughputSummary>("/telemetry/websocket-throughput");
export const getFundingHistory = (venue?: string, symbol?: string, limit = 200) => {
  const params = new URLSearchParams();
  if (venue) params.append("venue", venue);
  if (symbol) params.append("symbol", symbol);
  params.append("limit", String(limit));
  return request<FundingSettlement[]>(`/funding-history?${params.toString()}`);
};
export const getSimulationAccount = () => request<SimulationAccount>("/simulation/account");
export const resetSimulation = () =>
  request<{ status: string; message: string; account: SimulationAccount }>("/simulation/reset", {
    method: "POST",
  });
export const openPosition = (opportunity_id: string, capital_usd: number) =>
  request<Position>("/positions/open", {
    method: "POST",
    body: JSON.stringify({ opportunity_id, capital_usd, paper: true }),
  });
export const closePosition = (id: string) =>
  request<Position>(`/positions/${id}/close`, { method: "POST", body: JSON.stringify({}) });
export const simulate = (opportunity_id: string, capital_usd: number, holding_days: number) =>
  request<Simulation>("/simulator", {
    method: "POST",
    body: JSON.stringify({ opportunity_id, capital_usd, holding_days }),
  });
export const updateSettings = (values: Partial<Settings>) =>
  request<Settings>("/settings", { method: "PATCH", body: JSON.stringify(values) });

export function connectMarketSocket(
  onMessage: (data: {
    opportunities: Opportunity[];
    positions: Position[];
    account?: SimulationAccount | null;
  }) => void
) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws/market`);
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data) as {
      type: string;
      data: {
        opportunities: Opportunity[];
        positions: Position[];
        account?: SimulationAccount | null;
      };
    };
    if (message.type === "market_update") onMessage(message.data);
  };
  return socket;
}
