from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


QuoteStatus = Literal["normal", "stale", "unavailable", "fallback", "error", "suspicious"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Quote:
    key: str
    value: float | None
    status: QuoteStatus
    source: str
    source_url: str
    last_success_time: str | None = None
    debug: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "status": self.status,
            "source": self.source,
            "source_url": self.source_url,
            "last_success_time": self.last_success_time,
            "debug": self.debug,
        }


SOURCE_META: dict[str, dict[str, str]] = {
    "max_usdt_twd": {
        "source": "MAX API",
        "source_url": "https://max-api.maicoin.com/api/v2/tickers/usdttwd",
    },
    "usd_twd_mid": {
        "source": "Bank of Taiwan cash rate",
        "source_url": "https://rate.bot.com.tw/xrt?Lang=zh-TW",
    },
    "cny_twd_mid": {
        "source": "Bank of Taiwan cash rate",
        "source_url": "https://rate.bot.com.tw/xrt?Lang=zh-TW",
    },
    "official_usd_cny": {
        "source": "PBC latest article",
        "source_url": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/index.html",
    },
    "okx_cny_usdt": {
        "source": "OKX P2P buy-usdt",
        "source_url": "https://www.okx.com/zh-hant/p2p-block/cny/buy-usdt",
    },
    "usdt_usd_ref": {
        "source": "Coinbase USDT/USD",
        "source_url": "https://api.exchange.coinbase.com/products/USDT-USD/ticker",
    },
    "gold_9999_buy_cny_g": {
        "source": "China Gold 9999",
        "source_url": "http://beijingrtj.com/",
    },
    "gold_9999_sell_cny_g": {
        "source": "China Gold 9999",
        "source_url": "http://beijingrtj.com/",
    },
    "gold_9999_mid_cny_g": {
        "source": "China Gold 9999",
        "source_url": "http://beijingrtj.com/",
    },
    "london_gold_buy_usd_oz": {
        "source": "London Gold XAU/USD",
        "source_url": "https://www.wfbullion.com/",
    },
    "london_gold_sell_usd_oz": {
        "source": "London Gold XAU/USD",
        "source_url": "https://www.wfbullion.com/",
    },
    "london_gold_mid_usd_oz": {
        "source": "London Gold XAU/USD",
        "source_url": "https://www.wfbullion.com/",
    },
    "london_gold_buy_cny_g": {
        "source": "London Gold XAU/USD",
        "source_url": "https://www.wfbullion.com/",
    },
    "london_gold_sell_cny_g": {
        "source": "London Gold XAU/USD",
        "source_url": "https://www.wfbullion.com/",
    },
    "london_gold_mid_cny_g": {
        "source": "London Gold XAU/USD",
        "source_url": "https://www.wfbullion.com/",
    },
}


def empty_quote(key: str, debug: dict[str, Any] | None = None) -> Quote:
    meta = SOURCE_META[key]
    return Quote(
        key=key,
        value=None,
        status="unavailable",
        source=meta["source"],
        source_url=meta["source_url"],
        last_success_time=None,
        debug=debug or {},
    )
