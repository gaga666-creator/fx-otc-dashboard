import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Area,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { RefreshCcw, Route } from "lucide-react";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE || "";
const tabs = ["USD/TWD", "CNY/TWD", "USD/CNY", "GOLD/CNY"];
const rangeOptions = [
  { key: "1D", label: "1天", hours: 24 },
  { key: "7D", label: "7天", hours: 168 },
  { key: "1M", label: "近1個月", hours: 720 },
  { key: "3M", label: "近3個月", hours: 2160 },
  { key: "1Y", label: "1年", hours: 8760 },
];
const sourceKeys = ["max_usdt_twd", "okx_cny_usdt", "usd_twd_mid", "cny_twd_mid", "official_usd_cny", "usdt_usd_ref"];
const quoteAliases = {
  usdt_usd_ref: ["usdt_usd_ref", "coinbase_usdt_usd", "cmc_usdt_usd"],
};

const emptyQuote = {
  value: null,
  status: "unavailable",
  source: "",
  source_url: "",
  last_success_time: null,
  debug: {},
};

function formatRate(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(4);
}

function formatSpread(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const sign = Number(value) > 0 ? "+" : "";
  return `${sign}${Number(value).toFixed(4)} TWD/USDT`;
}

function formatSpreadValue(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const sign = Number(value) > 0 ? "+" : "";
  return `${sign}${Number(value).toFixed(4)}`;
}

function getArbPct(spread, cost) {
  if (
    spread === null ||
    spread === undefined ||
    cost === null ||
    cost === undefined ||
    Number.isNaN(Number(spread)) ||
    Number.isNaN(Number(cost)) ||
    Number(cost) <= 0
  ) {
    return null;
  }
  return (Number(spread) / Number(cost)) * 100;
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const sign = Number(value) > 0 ? "+" : "";
  return `${sign}${Number(value).toFixed(2)}%`;
}

function isQuoteAvailable(quote) {
  return ["normal", "stale", "fallback", "suspicious"].includes(quote?.status) && quote.value !== null && quote.value !== undefined;
}

function getRouteStatus(spread, routeReady) {
  if (!routeReady || spread === null || spread === undefined || Number.isNaN(Number(spread))) {
    return { label: "資料不足", tone: "insufficient" };
  }
  if (Number(spread) > 0.1) return { label: "可行", tone: "positive" };
  if (Number(spread) > 0) return { label: "觀察", tone: "watch" };
  return { label: "不利", tone: "negative" };
}

function formatDisplayTime(iso) {
  if (!iso) return "--";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "--";
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function displayTime(iso) {
  return formatDisplayTime(iso);
}

function pickQuote(data, key) {
  const keys = quoteAliases[key] || [key];
  for (const candidate of keys) {
    if (data?.[candidate]) return data[candidate];
  }
  return emptyQuote;
}

function statusTone(status) {
  return {
    normal: "bg-limeok text-ink",
    stale: "bg-amberwarn text-ink",
    fallback: "bg-cyanline text-ink",
    suspicious: "bg-amberwarn text-ink",
    unavailable: "bg-white/10 text-white/55",
    error: "bg-rosebad text-ink",
  }[status] || "bg-white/10 text-white/55";
}

function statusDot(status) {
  return {
    normal: "bg-limeok shadow-[0_0_16px_rgba(56,216,123,.8)]",
    stale: "bg-amberwarn shadow-[0_0_16px_rgba(251,191,36,.7)]",
    fallback: "bg-cyanline shadow-[0_0_16px_rgba(34,211,238,.75)]",
    suspicious: "bg-amberwarn shadow-[0_0_16px_rgba(251,191,36,.7)]",
    unavailable: "bg-rosebad shadow-[0_0_16px_rgba(251,113,133,.7)]",
    error: "bg-rosebad shadow-[0_0_16px_rgba(251,113,133,.75)]",
  }[status] || "bg-white/30";
}

async function getLatest() {
  const response = await fetch(`${API_BASE}/api/rates/latest`);
  if (!response.ok) throw new Error(`latest failed: ${response.status}`);
  return response.json();
}

async function postRefresh() {
  const response = await fetch(`${API_BASE}/api/admin/refresh`, { method: "POST" });
  if (!response.ok) throw new Error(`refresh failed: ${response.status}`);
  return response.json();
}

function getDataIntegrity(data) {
  const available = sourceKeys.filter((key) => {
    return isQuoteAvailable(pickQuote(data, key));
  }).length;
  return {
    available,
    total: sourceKeys.length,
    complete: available === sourceKeys.length,
  };
}

function getDecision(data) {
  const d = data?.derived || {};
  const dataIntegrity = getDataIntegrity(data);
  const max = pickQuote(data, "max_usdt_twd").value;
  const routeReadiness = {
    cny:
      isQuoteAvailable(pickQuote(data, "cny_twd_mid")) &&
      isQuoteAvailable(pickQuote(data, "okx_cny_usdt")) &&
      isQuoteAvailable(pickQuote(data, "max_usdt_twd")),
    usd:
      isQuoteAvailable(pickQuote(data, "usd_twd_mid")) &&
      isQuoteAvailable(pickQuote(data, "usdt_usd_ref")) &&
      isQuoteAvailable(pickQuote(data, "max_usdt_twd")),
  };
  const routes = [
    {
      key: "cny",
      name: "人民幣路徑",
      badge: "CNY/TWD",
      flow: ["TWD", "CNY", "USDT", "TWD"],
      cost: d.cny_path_cost,
      spread: d.cny_path_spread_vs_max,
      buyLabel: "台銀 CNY",
      buyValue: pickQuote(data, "cny_twd_mid").value,
      sellLabel: "OKX",
      sellValue: pickQuote(data, "okx_cny_usdt").value,
    },
    {
      key: "usd",
      name: "美元路徑",
      badge: "USD/TWD",
      flow: ["TWD", "USD", "USDT", "TWD"],
      cost: d.usd_path_cost,
      spread: d.usd_path_spread_vs_max,
      buyLabel: "台銀 USD",
      buyValue: pickQuote(data, "usd_twd_mid").value,
      sellLabel: "Coinbase",
      sellValue: pickQuote(data, "usdt_usd_ref").value,
    },
  ];
  const ranked = routes
    .filter((route) => route.spread !== null && route.spread !== undefined && !Number.isNaN(Number(route.spread)))
    .sort((a, b) => Number(b.spread) - Number(a.spread));
  const bestBase = ranked[0] || routes[0];
  const best = {
    ...bestBase,
    max,
    arbPct: getArbPct(bestBase?.spread, bestBase?.cost),
    status: getRouteStatus(bestBase?.spread, routeReadiness[bestBase?.key]),
  };
  const status = best.status;
  return {
    dataIntegrity,
    best,
    routes: routes.map((route) => ({
      ...route,
      max,
      arbPct: getArbPct(route.spread, route.cost),
      status: getRouteStatus(route.spread, routeReadiness[route.key]),
      isBest: bestBase?.key === route.key && route.spread !== null && route.spread !== undefined,
      lastUpdated: data?.meta?.last_refresh_time,
    })),
    status,
    lastUpdated: data?.meta?.last_refresh_time,
  };
}

function getBrowserTabStatus(data, decision) {
  const hasDataGap = !decision.dataIntegrity.complete;
  const hasStaleSource = sourceKeys.some((key) => pickQuote(data, key).status === "stale");
  const spread = Number(decision.best?.spread);
  const routeLabel = decision.best?.key === "usd" ? "USD" : "RMB";

  if (hasDataGap) {
    return { category: "data-gap", title: "Data Gap｜FX OTC" };
  }
  if (hasStaleSource) {
    return { category: "stale", title: "Stale｜FX OTC" };
  }
  if (Number.isFinite(spread) && spread >= 0.15) {
    return { category: `actionable-${routeLabel}`, title: `+${spread.toFixed(3)}｜${routeLabel} 可行` };
  }
  if (Number.isFinite(spread) && spread > 0) {
    return { category: "watch", title: `+${spread.toFixed(3)}｜Watch` };
  }
  return { category: "default", title: "FX / OTC Dashboard" };
}

function useBrowserTabStatus(data, decision) {
  const originalTitleRef = React.useRef(null);
  const lastCategoryRef = React.useRef("");

  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    if (originalTitleRef.current === null) {
      originalTitleRef.current = document.title;
    }
    return () => {
      if (originalTitleRef.current !== null) {
        document.title = originalTitleRef.current;
      }
    };
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const next = getBrowserTabStatus(data, decision);
    if (next.category !== lastCategoryRef.current) {
      document.title = next.title;
      lastCategoryRef.current = next.category;
    }
  }, [data, decision]);
}

function Shell({ children }) {
  return (
    <div className="min-h-screen bg-ink text-white">
      <div className="fixed inset-0 -z-10 bg-[radial-gradient(circle_at_16%_10%,rgba(34,211,238,.14),transparent_26%),radial-gradient(circle_at_84%_4%,rgba(56,216,123,.08),transparent_24%),linear-gradient(135deg,#071014,#0a1219_42%,#101014)]" />
      <main className="mx-auto max-w-6xl px-3 py-4 sm:px-6 lg:px-8">{children}</main>
    </div>
  );
}

function Header({ data, onRefresh, refreshing }) {
  return (
    <header id="s0" className="top-brand">
      <div className="wordmark" aria-label="FX OTC">
        <span className="wordmark-mark">FX</span>
        <span>
          <b>OTC</b>
          <small>Route Monitor</small>
        </span>
      </div>
      <div className="top-actions">
        <span className="top-time">{displayTime(data?.meta?.last_refresh_time)}</span>
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-cyanline/30 bg-cyanline/10 text-cyanline transition hover:bg-cyanline/20 focus:outline-none focus:ring-2 focus:ring-cyanline/70 disabled:opacity-45"
          aria-label="refresh"
        >
          <RefreshCcw size={18} className={refreshing ? "animate-spin" : ""} />
        </button>
      </div>
    </header>
  );
}

function RouteStatusDot({ status }) {
  return (
    <span className={`route-status-dot route-status-dot-${status.tone}`} aria-hidden="true" />
  );
}

function MarketTape({ data }) {
  const items = [
    ["PBC", "USD/CNY", pickQuote(data, "official_usd_cny"), "https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/index.html"],
    ["OKX", "CNY/USDT", pickQuote(data, "okx_cny_usdt"), "https://www.okx.com/zh-hant/p2p-block/cny/buy-usdt"],
    ["BOT USD", "USD/TWD", pickQuote(data, "usd_twd_mid"), "https://rate.bot.com.tw/xrt?Lang=zh-TW"],
    ["MAX", "TWD/USDT", pickQuote(data, "max_usdt_twd"), "https://max.maicoin.com/markets/usdttwd"],
    ["BOT CNY", "CNY/TWD", pickQuote(data, "cny_twd_mid"), "https://rate.bot.com.tw/xrt?Lang=zh-TW"],
    ["Coinbase", "USDT/USD", pickQuote(data, "usdt_usd_ref"), "https://exchange.coinbase.com/trade/USDT-USD"],
  ];
  return (
    <section className="market-tape" aria-label="market tape">
      {items.map(([label, unit, quote, href]) => {
        const Tag = href ? "a" : "div";
        return (
        <Tag className="tape-item" key={`${label}-${unit}`} href={href} target={href ? "_blank" : undefined} rel={href ? "noreferrer" : undefined}>
          <div className="tape-main-row">
            <span className={`tape-dot ${statusDot(quote.status)}`} />
            <b>{label}</b>
            <strong>{formatRate(quote.value)}</strong>
          </div>
          <small>{quote.status === "fallback" ? `${unit} / fallback` : unit}</small>
        </Tag>
      );})}
    </section>
  );
}

function BestRouteSummaryCard({ decision }) {
  const best = decision.best || {};
  return (
    <section className="best-summary-card">
      <div className="best-summary-main">
        <div className="best-summary-title">
          <RouteStatusDot status={best.status || { tone: "insufficient" }} />
          <h2>{best.name || "--"}</h2>
          <span className="route-mini-badge">{best.badge || "--"}</span>
        </div>
        <div className="best-summary-time">更新時間 {formatDisplayTime(decision.lastUpdated)}</div>
      </div>
      <div className="best-summary-metrics">
        <DecisionMetric label="理論成本" value={formatRate(best.cost)} />
        <DecisionMetric label="預估獲利 TWD/USDT" value={formatSpreadValue(best.spread)} tone={best.spread > 0 ? "positive" : "negative"} dominant />
        <DecisionMetric label="套利空間" value={formatPercent(best.arbPct)} tone={best.arbPct > 0 ? "positive" : "negative"} />
      </div>
    </section>
  );
}

function CurrencyFlow({ flow }) {
  return (
    <div className="currency-flow">
      {flow.map((currency, index) => (
        <React.Fragment key={`${currency}-${index}`}>
          <span>{currency}</span>
          {index < flow.length - 1 && <i>→</i>}
        </React.Fragment>
      ))}
    </div>
  );
}

function DecisionMetric({ label, value, tone = "", dominant = false }) {
  return (
    <div className={`decision-metric ${dominant ? "decision-metric-dominant" : ""}`}>
      <span>{label}</span>
      <b className={tone}>{value}</b>
    </div>
  );
}

function chartLines(tab) {
  if (tab === "USD/TWD") {
    return [
      ["usd_path_cost", "美元基準", "#facc15", "primary"],
      ["max_usdt_twd", "MAX 現貨", "#94a3b8", "secondary"],
      ["official_twd_usdt_cost", "官方基準", "#67e8f9", "secondary"],
    ];
  }
  if (tab === "CNY/TWD") {
    return [["cny_twd_mid", "台銀 CNY/TWD", "#facc15", "primary"]];
  }
  return [
    ["official_usd_cny", "官方 USD/CNY", "#facc15", "primary"],
    ["okx_cny_usdt", "OKX CNY/USDT", "#67e8f9", "secondary"],
  ];
}

function goldChartLines() {
  return [
    ["gold_9999_mid_cny_g", "黃金9999", "#facc15", "primary"],
    ["london_gold_mid_cny_g", "倫敦金", "#94a3b8", "secondary"],
  ];
}

function formatAxisTime(value, range) {
  const date = new Date(Number(value) || value);
  if (Number.isNaN(date.getTime())) return value;
  if (["7D", "1M", "3M", "1Y"].includes(range)) {
    return `${date.getMonth() + 1}/${date.getDate()}`;
  }
  return date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
}

function getXAxisTicks(hours) {
  const count = 7;
  const end = Date.now();
  const start = end - hours * 3600 * 1000;
  const step = (end - start) / (count - 1);
  return Array.from({ length: count }, (_, index) => Math.round(start + step * index));
}

function addMinutes(value, minutes) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  date.setMinutes(date.getMinutes() + minutes);
  return date.toISOString();
}

function normalizeChartRows(rows, hours) {
  const now = Date.now();
  const filtered = rows.filter((row) => now - new Date(row.time).getTime() <= hours * 3600 * 1000);
  const usable = filtered.length > 0 ? filtered : rows.slice(-1);
  if (usable.length === 1) {
    return [
      { ...usable[0], timeMs: new Date(usable[0].time).getTime() },
      {
        ...usable[0],
        time: addMinutes(usable[0].time, 1),
        timeMs: new Date(addMinutes(usable[0].time, 1)).getTime(),
      },
    ];
  }
  return usable.map((row) => ({ ...row, timeMs: new Date(row.time).getTime() }));
}

function hasSeriesData(rows, key) {
  return rows.some((row) => row[key] !== null && row[key] !== undefined && !Number.isNaN(Number(row[key])));
}

function getTightYAxisDomain(seriesValues, mode) {
  const values = seriesValues.map(Number).filter((value) => Number.isFinite(value));
  const decimals = mode === "GOLD/CNY" ? 2 : mode === "USD/CNY" ? 4 : 3;
  if (!values.length) {
    return { domain: ["auto", "auto"], tickFormatter: (value) => Number(value).toFixed(decimals), decimals };
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  const minPadding = mode === "GOLD/CNY" ? 0.5 : mode === "USD/CNY" ? 0.001 : 0.005;
  const padding = Math.max(range * 0.15, minPadding);
  return {
    domain: [min - padding, max + padding],
    tickFormatter: (value) => Number(value).toFixed(decimals),
    decimals,
  };
}

function ChartTooltip({ active, payload, label, tab, range }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};
  const values =
    tab === "GOLD/CNY"
      ? [
          ["黃金9999", formatRate(row.gold_9999_mid_cny_g)],
          ["倫敦金", formatRate(row.london_gold_mid_cny_g)],
        ]
      : [
          ["MAX", formatRate(row.max_usdt_twd)],
          ["RMB Cost", formatRate(row.cny_path_cost)],
          ["USD Cost", formatRate(row.usd_path_cost)],
          ["RMB Spread", formatSpread(row.cny_path_spread_vs_max)],
          ["USD Spread", formatSpread(row.usd_path_spread_vs_max)],
        ];
  return (
    <div className="chart-tooltip">
      <p>{formatAxisTime(label, range)}</p>
      {values.map(([name, value]) => (
        <div key={name}>
          <span>{name}</span>
          <b>{value}</b>
        </div>
      ))}
    </div>
  );
}

function ChartPanel({ data }) {
  const [tab, setTab] = useState(tabs[0]);
  const [range, setRange] = useState(rangeOptions[0].key);
  const selectedRange = rangeOptions.find((item) => item.key === range) || rangeOptions[0];
  const hours = selectedRange.hours;
  const lines = tab === "GOLD/CNY" ? goldChartLines() : chartLines(tab);
  const chartData = useMemo(() => {
    const rows = data?.history || [];
    return normalizeChartRows(rows, hours);
  }, [data, hours]);
  const visibleLines = lines.filter(([key]) => hasSeriesData(chartData, key));
  const hasEnough = chartData.length > 0 && visibleLines.length > 0;
  const xTicks = getXAxisTicks(hours);
  const yAxis = getTightYAxisDomain(
    chartData.flatMap((row) => visibleLines.map(([key]) => row[key])).filter((value) => value !== null && value !== undefined),
    tab,
  );
  const firstVisibleKey = visibleLines[0]?.[0];
  const benchmark = firstVisibleKey && chartData.length ? [...chartData].reverse().find((row) => row[firstVisibleKey] !== null && row[firstVisibleKey] !== undefined)?.[firstVisibleKey] : null;

  return (
    <section id="s1" className="chart-shell">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="chart-title">Market Spread</h2>
        </div>
        <div className="flex min-w-0 flex-wrap gap-2 overflow-visible">
          {tabs.map((item) => (
            <button key={item} onClick={() => setTab(item)} className={`tab ${tab === item ? "tab-active" : ""}`}>
              {item}
            </button>
          ))}
        </div>
        <div className="flex shrink-0 gap-1">
          {rangeOptions.map((item) => (
            <button key={item.key} onClick={() => setRange(item.key)} className={`range ${range === item.key ? "range-active" : ""}`}>
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <div className="chart-plot mt-5 h-[300px] sm:h-[390px]">
        {!hasEnough ? (
          <div className="grid h-full place-items-center rounded-md border border-dashed border-white/12 bg-black/20 text-sm text-white/45">
            目前尚無可顯示的走勢資料
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 34 }}>
              <CartesianGrid stroke="rgba(148,163,184,.09)" vertical={false} />
              <XAxis
                dataKey="timeMs"
                type="number"
                stroke="rgba(148,163,184,.7)"
                tick={{ fontSize: 11 }}
                ticks={xTicks}
                domain={[xTicks[0], xTicks[xTicks.length - 1]]}
                tickFormatter={(value) => formatAxisTime(value, range)}
                minTickGap={36}
              />
              <YAxis
                stroke="rgba(148,163,184,.7)"
                tick={{ fontSize: 11 }}
                width={54}
                domain={yAxis.domain}
                tickFormatter={yAxis.tickFormatter}
              />
              <Tooltip content={<ChartTooltip tab={tab} range={range} />} />
              {benchmark !== null && benchmark !== undefined && (
                <ReferenceLine
                  y={Number(benchmark)}
                  stroke="rgba(148,163,184,.52)"
                  strokeDasharray="5 5"
                  label={{ value: Number(benchmark).toFixed(yAxis.decimals), position: "insideLeft", fill: "#94a3b8", fontSize: 11 }}
                />
              )}
              {firstVisibleKey && <Area type="monotone" dataKey={firstVisibleKey} fill="rgba(34,211,238,.06)" stroke="transparent" />}
              {visibleLines.map(([key, name, color, role]) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  name={name}
                  stroke={color}
                  strokeWidth={role === "primary" ? 2.8 : 1.6}
                  strokeOpacity={role === "primary" ? 1 : 0.42}
                  dot={false}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
        {hasEnough && (
          <div className="chart-legend">
            {visibleLines.map(([, name, color]) => (
              <div key={name} className="legend-item">
                <span style={{ background: color }} />
                <b>{name}</b>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function QuoteCard({ title, unit, quote }) {
  return (
    <div className="quote-card quote-card-compact">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-medium leading-snug text-white/76 sm:text-sm">{title}</h3>
        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${statusDot(quote.status)}`} />
      </div>
      <div className="mt-3 flex min-w-0 items-end justify-between gap-2">
        <div className="min-w-0 font-mono text-[1.35rem] font-semibold leading-none tabular-nums text-white sm:text-3xl">
          {formatRate(quote.value)}
        </div>
        <div className="shrink-0 pb-0.5 font-mono text-[10px] text-white/42 sm:text-xs">{unit}</div>
      </div>
      <div className="mt-2 font-mono text-[9px] leading-4 text-white/28 sm:text-[10px]">
        {quote.source} / {displayTime(quote.last_success_time)}
      </div>
    </div>
  );
}

function Section({ id, title, children, className = "" }) {
  return (
    <section id={id} className={`mt-5 ${className}`}>
      <h2 className="mb-3 text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function RouteCard({ route }) {
  return (
    <div className={`route-card ${route.isBest ? "route-card-best" : ""}`}>
      <div className="route-card-topline" />
      <div className="route-card-heading">
        <div className="route-title-pack">
          <span className="route-card-icon"><Route size={17} /></span>
          <h3><RouteStatusDot status={route.status} /> {route.name}</h3>
          <span className="route-mini-badge">{route.badge}</span>
        </div>
        <span className={`route-arb-pct ${route.arbPct > 0 ? "positive" : route.arbPct < 0 ? "negative" : ""}`}>
          {formatPercent(route.arbPct)}
        </span>
      </div>
      <CurrencyFlow flow={route.flow} />
      <div className="route-metrics">
        <DecisionMetric label="理論成本" value={formatRate(route.cost)} />
        <DecisionMetric label="MAX TWD/USDT" value={formatRate(route.max)} />
        <DecisionMetric label="預估獲利 TWD/USDT" value={formatSpreadValue(route.spread)} tone={route.spread > 0 ? "positive" : "negative"} dominant />
      </div>
      <div className="route-card-time">更新時間 {formatDisplayTime(route.lastUpdated)}</div>
    </div>
  );
}

function GoldReference({ data }) {
  const items = [
    {
      label: "黃金9999",
      buy: pickQuote(data, "gold_9999_buy_cny_g"),
      sell: pickQuote(data, "gold_9999_sell_cny_g"),
      mid: pickQuote(data, "gold_9999_mid_cny_g"),
      href: "http://beijingrtj.com/",
    },
    {
      label: "倫敦金",
      buy: pickQuote(data, "london_gold_buy_cny_g"),
      sell: pickQuote(data, "london_gold_sell_cny_g"),
      mid: pickQuote(data, "london_gold_mid_cny_g"),
      href: "https://www.wfbullion.com/",
    },
  ];
  return (
    <div className="gold-reference" aria-label="gold reference">
      {items.map(({ label, buy, sell, mid, href }) => (
        <a className="gold-card" key={label} href={href} target="_blank" rel="noreferrer">
          <div className="gold-card-title">
            <span className={`tape-dot ${statusDot(mid.status)}`} />
            <span>{label}</span>
          </div>
          <div className="gold-card-prices">
            <span>買價 <b>{formatRate(buy.value)}</b></span>
            <span>賣價 <b>{formatRate(sell.value)}</b></span>
            <span>中價 <b>{formatRate(mid.value)}</b> <em>CNY/g</em></span>
          </div>
        </a>
      ))}
    </div>
  );
}

function RouteComparison({ decision, data }) {
  const orderedRoutes = [...decision.routes].sort((a, b) => (a.key === "usd" ? -1 : b.key === "usd" ? 1 : 0));
  return (
    <Section id="s2" title="理論成本" className="route-section">
      <div className="route-cards-grid">
        {orderedRoutes.map((route) => (
          <RouteCard key={route.key} route={route} />
        ))}
      </div>
    </Section>
  );
}

function Calculator({ data }) {
  const cnyMid = pickQuote(data, "cny_twd_mid").value;
  const okx = pickQuote(data, "okx_cny_usdt").value;
  const max = pickQuote(data, "max_usdt_twd").value;
  const [rate, setRate] = useState("");
  const [buy, setBuy] = useState("");
  const [usdtTwd, setUsdtTwd] = useState("");

  useEffect(() => {
    setRate(cnyMid === null || cnyMid === undefined ? "" : formatRate(cnyMid));
    setBuy(okx === null || okx === undefined ? "" : formatRate(okx));
    setUsdtTwd(max === null || max === undefined ? "" : formatRate(max));
  }, [cnyMid, okx, max]);

  const cost = Number(rate) * Number(buy);
  const profit = !Number.isFinite(Number(usdtTwd)) || !Number.isFinite(cost) ? null : Number(usdtTwd) - cost;
  const arbPct = getArbPct(profit, Number.isFinite(cost) ? cost : null);

  return (
    <Section
      id="s4"
      title={
        <span className="calculator-title">
          匯率計算機
          <span className={`calculator-arb-pct ${arbPct > 0 ? "positive" : arbPct < 0 ? "negative" : ""}`}>
            套利空間 {formatPercent(arbPct)}
          </span>
        </span>
      }
    >
      <div className="calculator-card">
        <div className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="field">
              <span>TWD / CNY 匯率</span>
              <input value={rate} onChange={(event) => setRate(event.target.value)} inputMode="decimal" />
            </label>
            <label className="field">
              <span>CNY / USDT 買價</span>
              <input value={buy} onChange={(event) => setBuy(event.target.value)} inputMode="decimal" />
            </label>
            <label className="field">
              <span>USDT / TWD價格</span>
              <input value={usdtTwd} onChange={(event) => setUsdtTwd(event.target.value)} inputMode="decimal" />
            </label>
            <button
              className="reset-button sm:col-span-3"
              onClick={() => {
                setRate(cnyMid === null || cnyMid === undefined ? "" : formatRate(cnyMid));
                setBuy(okx === null || okx === undefined ? "" : formatRate(okx));
                setUsdtTwd(max === null || max === undefined ? "" : formatRate(max));
              }}
            >
              重置為預設市場價
            </button>
          </div>
          <div className="calculator-results">
            <DecisionMetric label="理論成本" value={formatRate(Number.isFinite(cost) ? cost : null)} />
            <DecisionMetric label="USDT / TWD價格" value={formatRate(usdtTwd)} />
            <DecisionMetric label="預估利潤" value={formatSpread(profit)} tone={profit > 0 ? "positive" : "negative"} dominant />
          </div>
        </div>
      </div>
    </Section>
  );
}

function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    try {
      setData(await getLatest());
      setError("");
    } catch (err) {
      setError(err.message);
    }
  }

  async function refresh() {
    setRefreshing(true);
    try {
      await postRefresh();
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const decision = getDecision(data);
  useBrowserTabStatus(data, decision);

  return (
    <Shell>
      <Header data={data} onRefresh={refresh} refreshing={refreshing} />
      <MarketTape data={data} />
      <BestRouteSummaryCard decision={decision} />
      {error && <div className="mb-4 rounded-md border border-rosebad/40 bg-rosebad/10 p-3 text-sm text-rosebad">{error}</div>}
      <ChartPanel data={data} />
      <RouteComparison decision={decision} data={data} />
      <Section id="s3" title="黃金參考" className="gold-section">
        <GoldReference data={data} />
      </Section>
      <Calculator data={data} />
    </Shell>
  );
}

createRoot(document.getElementById("root")).render(<App />);
