from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from . import db
from .fetchers.sources import fetch_bot_rates, fetch_gold_9999, fetch_london_gold, fetch_max, fetch_okx, fetch_pbc, fetch_usdt_usd_ref
from .models import Quote, SOURCE_META, now_iso


load_dotenv()

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if origin.strip()
]

app = FastAPI(title="FX / OTC Dashboard API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    db.init_db()


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def derive(quotes: dict[str, Quote]) -> dict[str, float | None]:
    def val(key: str) -> float | None:
        q = quotes[key]
        return q.value if q.status in {"normal", "stale", "fallback"} else None

    max_usdt_twd = val("max_usdt_twd")
    usd_twd_mid = val("usd_twd_mid")
    cny_twd_mid = val("cny_twd_mid")
    official_usd_cny = val("official_usd_cny")
    okx_cny_usdt = val("okx_cny_usdt")
    usdt_usd_ref = val("usdt_usd_ref")

    official_cost = cny_twd_mid * official_usd_cny if cny_twd_mid is not None and official_usd_cny is not None else None
    usd_cost = usd_twd_mid * usdt_usd_ref if usd_twd_mid is not None and usdt_usd_ref is not None else None
    cny_cost = cny_twd_mid * okx_cny_usdt if cny_twd_mid is not None and okx_cny_usdt is not None else None
    return {
        "official_twd_usdt_cost": _round(official_cost),
        "usd_path_cost": _round(usd_cost),
        "cny_path_cost": _round(cny_cost),
        "cny_path_spread_vs_max": _round(max_usdt_twd - cny_cost if max_usdt_twd is not None and cny_cost is not None else None),
        "usd_path_spread_vs_max": _round(max_usdt_twd - usd_cost if max_usdt_twd is not None and usd_cost is not None else None),
    }


def chart_history() -> list[dict[str, Any]]:
    rows = db.snapshots(168)
    points: dict[str, dict[str, Any]] = {}
    for row in rows:
        minute = row["created_at"][:16]
        points.setdefault(minute, {"time": minute})
        points[minute][row["source_key"]] = row["value"] if row["status"] in {"normal", "stale", "fallback"} else None
    output = []
    for point in points.values():
        q = {key: Quote(key=key, value=point.get(key), status="normal", source="", source_url="") for key in SOURCE_META}
        derived = derive(q)
        output.append({**point, **derived})
    return output


async def refresh_all() -> dict[str, Any]:
    previous = db.get_latest()
    bot_task = fetch_bot_rates(previous)
    max_task = fetch_max(previous.get("max_usdt_twd"))
    pbc_task = fetch_pbc(previous.get("official_usd_cny"))
    okx_task = fetch_okx(previous.get("okx_cny_usdt"))
    usdt_ref_task = fetch_usdt_usd_ref(previous.get("usdt_usd_ref"))
    gold_9999_task = fetch_gold_9999(previous)
    max_quote, bot_quotes, pbc_quote, okx_quote, usdt_ref_quote, gold_9999_quotes = await asyncio.gather(
        max_task,
        bot_task,
        pbc_task,
        okx_task,
        usdt_ref_task,
        gold_9999_task,
    )
    official_for_gold = pbc_quote.value if pbc_quote.status in {"normal", "stale"} else previous.get("official_usd_cny").value if previous.get("official_usd_cny") else None
    london_gold_quotes = await fetch_london_gold(previous, official_for_gold)
    quotes: dict[str, Quote] = {
        "max_usdt_twd": max_quote,
        **bot_quotes,
        "official_usd_cny": pbc_quote,
        "okx_cny_usdt": okx_quote,
        "usdt_usd_ref": usdt_ref_quote,
        **gold_9999_quotes,
        **london_gold_quotes,
    }
    refreshed_at = now_iso()
    db.save_quotes(quotes.values(), refreshed_at)
    result = {
        "refreshed_at": refreshed_at,
        "sources": {key: {"status": quote.status, "value": quote.value, "debug": quote.debug} for key, quote in quotes.items()},
    }
    db.save_refresh_log(refreshed_at, result)
    return result


async def ensure_initial_data() -> None:
    latest = db.get_latest()
    if all(q.value is None for q in latest.values()):
        await refresh_all()


@app.get("/api/rates/latest")
async def latest_rates() -> dict[str, Any]:
    await ensure_initial_data()
    quotes = db.get_latest()
    return {
        **{key: quotes[key].public() for key in SOURCE_META},
        "derived": derive(quotes),
        "history": chart_history(),
        "meta": {
            "last_refresh_time": db.last_refresh_time(),
            "unavailable_count": sum(1 for q in quotes.values() if q.status == "unavailable"),
            "source_count": len(quotes),
        },
    }


@app.post("/api/admin/refresh")
async def admin_refresh() -> dict[str, Any]:
    return await refresh_all()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    quotes = db.get_latest()
    return {
        "status": "ok",
        "last_refresh_time": db.last_refresh_time(),
        "sources": {key: quote.status for key, quote in quotes.items()},
        "production": os.getenv("ENV", "development").lower() == "production",
        "cache": "sqlite",
        "db_path": str(db.db_path()),
    }


def fmt(value: float | None, digits: int = 4) -> str:
    return "--" if value is None else f"{value:.{digits}f}"


@app.get("/api/telegram/summary", response_class=PlainTextResponse)
async def telegram_summary() -> str:
    await ensure_initial_data()
    quotes = db.get_latest()
    derived = derive(quotes)
    return "\n".join(
        [
            "外匯 / OTC 快速報價",
            f"MAX USDT/TWD：{fmt(quotes['max_usdt_twd'].value)}",
            f"OKX OTC CNY/USDT：{fmt(quotes['okx_cny_usdt'].value)}",
            f"台銀 USD/TWD 中價：{fmt(quotes['usd_twd_mid'].value)}",
            f"台銀 CNY/TWD 中價：{fmt(quotes['cny_twd_mid'].value)}",
            f"官方 USD/CNY：{fmt(quotes['official_usd_cny'].value)}",
            "",
            "路徑比較：",
            f"人民幣路徑成本：{fmt(derived['cny_path_cost'])}",
            f"美元路徑成本：{fmt(derived['usd_path_cost'])}",
            "",
            f"更新時間：{db.last_refresh_time() or datetime.now(timezone.utc).isoformat()}",
            "資料僅供參考",
        ]
    )
