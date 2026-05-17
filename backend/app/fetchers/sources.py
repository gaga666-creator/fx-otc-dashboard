from __future__ import annotations

import asyncio
import os
import re
from statistics import mean
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..models import Quote, SOURCE_META, empty_quote, now_iso


TIMEOUT = httpx.Timeout(20.0, connect=10.0)
TROY_OUNCE_GRAMS = 31.1034768
PLAYWRIGHT_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
]
PLAYWRIGHT_ALLOWED_RESOURCE_TYPES = {"document", "script", "xhr", "fetch"}
_PLAYWRIGHT_LOCK = asyncio.Lock()
_PLAYWRIGHT_MANAGER = None
_PLAYWRIGHT_BROWSER = None
_PLAYWRIGHT_CONTEXT = None
_PLAYWRIGHT_CLOSE_TASK: asyncio.Task | None = None


def _normal(key: str, value: float, debug: dict[str, Any] | None = None, source_url: str | None = None) -> Quote:
    meta = SOURCE_META[key]
    return Quote(
        key=key,
        value=value,
        status="normal",
        source=meta["source"],
        source_url=source_url or meta["source_url"],
        last_success_time=now_iso(),
        debug=debug or {},
    )


def _suspicious(key: str, value: float, debug: dict[str, Any] | None = None, source_url: str | None = None) -> Quote:
    meta = SOURCE_META[key]
    return Quote(
        key=key,
        value=value,
        status="suspicious",
        source=meta["source"],
        source_url=source_url or meta["source_url"],
        last_success_time=now_iso(),
        debug=debug or {},
    )


def _failed_with_previous(key: str, previous: Quote | None, debug: dict[str, Any] | None = None) -> Quote:
    meta = SOURCE_META[key]
    merged_debug = dict(previous.debug if previous else {})
    merged_debug.update(debug or {})
    source_url = (debug or {}).get("source_url") or (previous.source_url if previous else None) or meta["source_url"]
    if previous and previous.value is not None and previous.last_success_time:
        return Quote(
            key=key,
            value=previous.value,
            status="stale",
            source=meta["source"],
            source_url=source_url,
            last_success_time=previous.last_success_time,
            debug=merged_debug,
        )
    return Quote(
        key=key,
        value=None,
        status="unavailable",
        source=meta["source"],
        source_url=source_url,
        last_success_time=None,
        debug=merged_debug,
    )


def _failed_group(keys: list[str], previous: dict[str, Quote] | None, debug: dict[str, Any]) -> dict[str, Quote]:
    previous = previous or {}
    return {key: _failed_with_previous(key, previous.get(key), debug) for key in keys}


def _numbers_after_label(text: str, label: str) -> list[float]:
    index = text.find(label)
    if index < 0:
        return []
    start = index + len(label)
    chunk = text[start : start + 220]
    return [float(item) for item in re.findall(r"(?<!\d)(\d{2,5}(?:\.\d{1,4})?)(?!\d)", chunk)]


def _excerpt_after_label(text: str, label: str, length: int = 220) -> str:
    index = text.find(label)
    if index < 0:
        return text[:length]
    return text[index : index + length]


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r\n", " ").replace("\n", " ").replace("\t", " ")).strip()


def parse_beijingrtj_gold_prices(text: str) -> dict[str, Any]:
    normalized = _compact_text(text)
    row_match = re.search(
        r"(?:商品\s*)?(?:回购|回購)\s*(?:销售|銷售)\s*(?:时间|時間)\s*黄金\s*(\d{3,5}(?:\.\d{1,4})?)\s*(\d{3,5}(?:\.\d{1,4})?)",
        normalized,
    )
    if not row_match:
        row_match = re.search(
            r"黄金\s*(\d{3,5}(?:\.\d{1,4})?)\s*(\d{3,5}(?:\.\d{1,4})?)\s*\d{1,2}:\d{2}",
            normalized,
        )
    if row_match:
        repurchase = float(row_match.group(1))
        sale = float(row_match.group(2))
        raw_prices = [
            float(value)
            for value in re.findall(r"(?<!\d)(\d{3,5}(?:\.\d{1,4})?)(?!\d)", normalized)
            if 500 <= float(value) <= 1500
        ]
        return {
            "buy_price": repurchase,
            "sell_price": sale,
            "mid_price": (repurchase + sale) / 2,
            "raw_prices": raw_prices[:12],
            "parser_patterns_used": ["beijingrtj_gold_row_repurchase_sale"],
            "raw_text_excerpt": normalized[max(row_match.start() - 80, 0) : row_match.end() + 160],
        }

    patterns = {
        "buy_price": [
            r"(?:黄金买价|黃金買價|买价|買價|销售价|銷售價|出售价|出售價)\D{0,24}(\d{3,5}(?:\.\d{1,4})?)",
            r"(?:销售|銷售|出售)\D{0,16}(\d{3,5}(?:\.\d{1,4})?)",
        ],
        "sell_price": [
            r"(?:黄金卖价|黃金賣價|卖价|賣價|回购价|回購價|回收价|回收價)\D{0,24}(\d{3,5}(?:\.\d{1,4})?)",
            r"(?:回购|回購|回收)\D{0,16}(\d{3,5}(?:\.\d{1,4})?)",
        ],
    }
    parsed: dict[str, float | None] = {"buy_price": None, "sell_price": None}
    used_patterns: list[str] = []
    for key, key_patterns in patterns.items():
        for pattern in key_patterns:
            match = re.search(pattern, normalized)
            if not match:
                continue
            price = float(match.group(1))
            if 500 <= price <= 1500:
                parsed[key] = price
                used_patterns.append(pattern)
                break

    raw_prices = [
        float(value)
        for value in re.findall(r"(?<!\d)(\d{3,5}(?:\.\d{1,4})?)(?!\d)", normalized)
        if 500 <= float(value) <= 1500
    ]
    buy = parsed["buy_price"]
    sell = parsed["sell_price"]
    return {
        "buy_price": buy,
        "sell_price": sell,
        "mid_price": (buy + sell) / 2 if buy is not None and sell is not None else None,
        "raw_prices": raw_prices[:12],
        "parser_patterns_used": used_patterns,
        "raw_text_excerpt": normalized[:500],
    }


def parse_quoteh5_gold_9999_prices(text: str) -> dict[str, Any]:
    normalized = _compact_text(text)
    row_match = re.search(
        r"(?:黄金|黃金|Gold)\s*9999.{0,80}?(\d{3,5}(?:\.\d{1,4})?)\D+(\d{3,5}(?:\.\d{1,4})?)",
        normalized,
        re.I,
    )
    if not row_match:
        row_match = re.search(
            r"(?:9999).{0,80}?(\d{3,5}(?:\.\d{1,4})?)\D+(\d{3,5}(?:\.\d{1,4})?)",
            normalized,
            re.I,
        )
    raw_prices = [
        float(value)
        for value in re.findall(r"(?<!\d)(\d{3,5}(?:\.\d{1,4})?)(?!\d)", normalized)
        if 500 <= float(value) <= 1500
    ]
    if not row_match:
        return {
            "buy_price": None,
            "sell_price": None,
            "mid_price": None,
            "raw_prices": raw_prices[:12],
            "parser_patterns_used": [],
            "raw_text_excerpt": normalized[:500],
        }
    first = float(row_match.group(1))
    second = float(row_match.group(2))
    if not (500 <= first <= 1500 and 500 <= second <= 1500):
        first = second = None
    buy = max(first, second) if first is not None and second is not None else None
    sell = min(first, second) if first is not None and second is not None else None
    return {
        "buy_price": buy,
        "sell_price": sell,
        "mid_price": (buy + sell) / 2 if buy is not None and sell is not None else None,
        "raw_prices": raw_prices[:12],
        "parser_patterns_used": ["quoteh5_gold9999_row_first_two_prices"],
        "raw_text_excerpt": normalized[max(row_match.start() - 80, 0) : row_match.end() + 160],
    }


async def _close_shared_playwright(delay: float = 2.0) -> None:
    global _PLAYWRIGHT_MANAGER, _PLAYWRIGHT_BROWSER, _PLAYWRIGHT_CONTEXT
    await asyncio.sleep(delay)
    context = _PLAYWRIGHT_CONTEXT
    browser = _PLAYWRIGHT_BROWSER
    manager = _PLAYWRIGHT_MANAGER
    _PLAYWRIGHT_CONTEXT = None
    _PLAYWRIGHT_BROWSER = None
    _PLAYWRIGHT_MANAGER = None
    try:
        if context:
            await context.close()
    except Exception:
        pass
    try:
        if browser:
            await browser.close()
    except Exception:
        pass
    try:
        if manager:
            await manager.stop()
    except Exception:
        pass


async def _new_low_memory_page():
    global _PLAYWRIGHT_MANAGER, _PLAYWRIGHT_BROWSER, _PLAYWRIGHT_CONTEXT, _PLAYWRIGHT_CLOSE_TASK
    if _PLAYWRIGHT_CLOSE_TASK and not _PLAYWRIGHT_CLOSE_TASK.done():
        _PLAYWRIGHT_CLOSE_TASK.cancel()
        _PLAYWRIGHT_CLOSE_TASK = None
    if _PLAYWRIGHT_MANAGER is None or _PLAYWRIGHT_BROWSER is None or _PLAYWRIGHT_CONTEXT is None:
        from playwright.async_api import async_playwright

        _PLAYWRIGHT_MANAGER = await async_playwright().start()
        _PLAYWRIGHT_BROWSER = await _PLAYWRIGHT_MANAGER.chromium.launch(headless=True, args=PLAYWRIGHT_LAUNCH_ARGS)
        _PLAYWRIGHT_CONTEXT = await _PLAYWRIGHT_BROWSER.new_context(locale="zh-TW", timezone_id="Asia/Taipei", viewport={"width": 1280, "height": 760})

        async def abort_heavy_assets(route):
            if route.request.resource_type in PLAYWRIGHT_ALLOWED_RESOURCE_TYPES:
                await route.continue_()
            else:
                await route.abort()

        await _PLAYWRIGHT_CONTEXT.route("**/*", abort_heavy_assets)
    return await _PLAYWRIGHT_CONTEXT.new_page()


async def _playwright_body_text(url: str, wait_ms: int = 8000) -> str:
    async with _PLAYWRIGHT_LOCK:
        page = await _new_low_memory_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(min(wait_ms, 9000))
            return await page.locator("body").inner_text(timeout=8000)
        finally:
            await page.close()
            await _close_shared_playwright(0)


async def fetch_max(previous: Quote | None = None) -> Quote:
    key = "max_usdt_twd"
    single_url = "https://max-api.maicoin.com/api/v2/tickers/usdttwd"
    all_url = "https://max-api.maicoin.com/api/v2/tickers"
    errors: list[str] = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 FX OTC Dashboard", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            try:
                resp = await client.get(single_url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return _normal(
                    key,
                    float(data["last"]),
                    {"raw_last": data.get("last"), "endpoint": single_url, "fallback_used": False},
                    source_url=single_url,
                )
            except Exception as exc:
                errors.append(f"{single_url}: {exc}")

            resp = await client.get(all_url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            last = data["usdttwd"]["last"]
            return _normal(
                key,
                float(last),
                {"raw_last": last, "endpoint": all_url, "fallback_used": True, "errors": errors},
                source_url=all_url,
            )
    except Exception as exc:
        errors.append(str(exc))
        return _failed_with_previous(key, previous, {"error": str(exc), "errors": errors})


async def fetch_bot_rates(previous: dict[str, Quote] | None = None) -> dict[str, Quote]:
    previous = previous or {}
    url = SOURCE_META["usd_twd_mid"]["source_url"]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        parsed: dict[str, tuple[float, float]] = {}
        for row in soup.select("table tbody tr"):
            text = " ".join(row.get_text(" ", strip=True).split())
            code = None
            if "USD" in text:
                code = "USD"
            elif "CNY" in text:
                code = "CNY"
            if not code:
                continue
            cells = [cell.get_text(" ", strip=True).replace(",", "") for cell in row.select("td")]
            numbers = []
            for cell in cells:
                try:
                    numbers.append(float(cell))
                except ValueError:
                    pass
            if len(numbers) >= 2:
                parsed[code] = (numbers[0], numbers[1])
        results: dict[str, Quote] = {}
        for code, key in [("USD", "usd_twd_mid"), ("CNY", "cny_twd_mid")]:
            if code not in parsed:
                raise ValueError(f"{code} cash row not found")
            buy, sell = parsed[code]
            results[key] = _normal(key, (buy + sell) / 2, {"cash_buy": buy, "cash_sell": sell})
        return results
    except Exception as exc:
        return {
            "usd_twd_mid": _failed_with_previous("usd_twd_mid", previous.get("usd_twd_mid"), {"error": str(exc)}),
            "cny_twd_mid": _failed_with_previous("cny_twd_mid", previous.get("cny_twd_mid"), {"error": str(exc)}),
        }


async def fetch_pbc(previous: Quote | None = None) -> Quote:
    key = "official_usd_cny"
    list_url = SOURCE_META[key]["source_url"]
    try:
        headers = {"User-Agent": "Mozilla/5.0 FX OTC Dashboard"}
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers=headers) as client:
            list_resp = await client.get(list_url)
            list_resp.raise_for_status()
            list_resp.encoding = list_resp.encoding or "utf-8"
            soup = BeautifulSoup(list_resp.text, "lxml")
            candidates = []
            for link in soup.find_all("a", href=True):
                label = link.get_text(" ", strip=True)
                date_match = re.search(r"(20\d{2})[-/\.年](\d{1,2})[-/\.月](\d{1,2})", label)
                if ("人民币汇率中间价" in label or ("人民币" in label and "汇率" in label and "中间价" in label)) and date_match:
                    href = urljoin(list_url, link["href"])
                    candidates.append((label, href, date_match.group(0)))
            if not candidates:
                raise ValueError("latest PBC article link not found")
            label, article_url, article_date = candidates[0]
            article_resp = await client.get(article_url)
            article_resp.raise_for_status()
            article_resp.encoding = article_resp.encoding or "utf-8"
        article_text = BeautifulSoup(article_resp.text, "lxml").get_text(" ", strip=True)
        match = re.search(r"1\s*美元\s*对\s*人民币\s*([0-9.]+)\s*元", article_text)
        if not match:
            raise ValueError("USD/CNY pattern not found in article body")
        start = max(match.start() - 40, 0)
        end = min(match.end() + 40, len(article_text))
        return _normal(
            key,
            float(match.group(1)),
            {
                "article_url": article_url,
                "article_date": article_date or label,
                "raw_excerpt": article_text[start:end],
            },
            source_url=article_url,
        )
    except Exception as exc:
        return _failed_with_previous(
            key,
            previous,
            {"error": str(exc), "article_url": None, "article_date": None, "raw_excerpt": None},
        )


async def fetch_usdt_usd_ref(previous: Quote | None = None) -> Quote:
    key = "usdt_usd_ref"
    url = SOURCE_META[key]["source_url"]
    try:
        headers = {"User-Agent": "Mozilla/5.0 FX OTC Dashboard", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        price = data["price"]
        return _normal(key, float(price), {"provider": "coinbase", "raw_price": price}, source_url=url)
    except Exception as exc:
        return _failed_with_previous(key, previous, {"error": str(exc)})


async def fetch_gold_9999(previous: dict[str, Quote] | None = None) -> dict[str, Quote]:
    keys = ["gold_9999_buy_cny_g", "gold_9999_sell_cny_g", "gold_9999_mid_cny_g"]
    primary_url = SOURCE_META["gold_9999_mid_cny_g"]["source_url"]
    fallback_url = "https://i.jzj9999.com/quoteh5"
    errors: list[str] = []
    text = ""
    mode = "httpx"
    parsed: dict[str, Any] = {}
    source_url = primary_url
    fallback_used = False

    async def fetch_text_with_httpx(source_url: str) -> str:
        headers = {"User-Agent": "Mozilla/5.0 FX OTC Dashboard"}
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers=headers) as client:
            resp = await client.get(source_url)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml").get_text("\n", strip=True)

    attempts = [
        (primary_url, parse_beijingrtj_gold_prices, "beijingrtj"),
        (fallback_url, parse_quoteh5_gold_9999_prices, "quoteh5"),
    ]
    for attempt_url, parser, label in attempts:
        source_url = attempt_url
        try:
            mode = f"httpx:{label}"
            text = await fetch_text_with_httpx(attempt_url)
            parsed = parser(text)
            if parsed.get("buy_price") is None or parsed.get("sell_price") is None:
                raise ValueError(f"{label} gold buy/sell price not found in static HTML")
            fallback_used = attempt_url == fallback_url
            break
        except Exception as exc:
            errors.append(f"httpx:{label}: {exc}")
            try:
                mode = f"playwright:{label}"
                text = await _playwright_body_text(attempt_url)
                parsed = parser(text)
                if parsed.get("buy_price") is None or parsed.get("sell_price") is None:
                    raise ValueError(f"{label} gold buy/sell price not found in rendered page")
                fallback_used = attempt_url == fallback_url
                break
            except Exception as render_exc:
                errors.append(f"playwright:{label}: {render_exc}")
                parsed = parsed or {}
    else:
        return _failed_group(
            keys,
            previous,
            {
                "error": "; ".join(errors),
                "error_reason": "gold9999 buy/sell price not found in primary or fallback source",
                "primary_source_url": primary_url,
                "fallback_source_url": fallback_url,
                "fallback_used": False,
                "raw_text_excerpt": parsed.get("raw_text_excerpt") or _compact_text(text)[:500] if text else "",
                "raw_prices": parsed.get("raw_prices", []),
                "buy_price": parsed.get("buy_price"),
                "sell_price": parsed.get("sell_price"),
                "mid_price": parsed.get("mid_price"),
                "fetch_mode": mode,
                "source_url": source_url,
            },
        )

    buy = parsed["buy_price"]
    sell = parsed["sell_price"]
    mid = parsed["mid_price"]
    debug = {
        "buy_price": buy,
        "sell_price": sell,
        "mid_price": mid,
        "raw_prices": parsed.get("raw_prices", []),
        "parsed_row": "gold9999 buy/sell row",
        "parser_patterns_used": parsed.get("parser_patterns_used", []),
        "raw_text_excerpt": parsed.get("raw_text_excerpt") or _compact_text(text)[:500],
        "error_reason": None,
        "fetch_mode": mode,
        "source_url": source_url,
        "primary_source_url": primary_url,
        "fallback_source_url": fallback_url,
        "fallback_used": fallback_used,
    }
    return {
        "gold_9999_buy_cny_g": _normal("gold_9999_buy_cny_g", buy, debug, source_url=source_url),
        "gold_9999_sell_cny_g": _normal("gold_9999_sell_cny_g", sell, debug, source_url=source_url),
        "gold_9999_mid_cny_g": _normal("gold_9999_mid_cny_g", mid, debug, source_url=source_url),
    }


async def fetch_london_gold(previous: dict[str, Quote] | None = None, official_usd_cny: float | None = None) -> dict[str, Quote]:
    keys = [
        "london_gold_buy_usd_oz",
        "london_gold_sell_usd_oz",
        "london_gold_mid_usd_oz",
        "london_gold_buy_cny_g",
        "london_gold_sell_cny_g",
        "london_gold_mid_cny_g",
    ]
    url = SOURCE_META["london_gold_mid_cny_g"]["source_url"]
    fallback_url = "https://api.gold-api.com/price/XAU"
    errors: list[str] = []
    text = ""
    mode = "httpx"
    if official_usd_cny is None:
        return _failed_group(keys, previous, {"error": "official_usd_cny unavailable for conversion", "source_url": url})
    try:
        headers = {"User-Agent": "Mozilla/5.0 FX OTC Dashboard"}
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = BeautifulSoup(resp.text, "lxml").get_text("\n", strip=True)
        values = _numbers_after_label(text, "倫敦金") or _numbers_after_label(text, "London Gold")
        if len(values) < 2:
            raise ValueError("london gold row not found in static HTML")
    except Exception as exc:
        errors.append(f"httpx: {exc}")
        try:
            mode = "playwright"
            text = await _playwright_body_text(url)
            values = _numbers_after_label(text, "倫敦金") or _numbers_after_label(text, "London Gold")
            if len(values) < 2:
                raise ValueError("london gold row not found in rendered page")
        except Exception as render_exc:
            errors.append(f"playwright: {render_exc}")
            try:
                mode = "gold-api"
                headers = {"User-Agent": "Mozilla/5.0 FX OTC Dashboard", "Accept": "application/json"}
                async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers=headers) as client:
                    resp = await client.get(fallback_url)
                    resp.raise_for_status()
                    data = resp.json()
                mid_usd = float(data["price"])
                mid_cny = mid_usd * official_usd_cny / TROY_OUNCE_GRAMS
                debug = {
                    "fallback_from": url,
                    "fallback_errors": errors,
                    "fallback_used": True,
                    "provider": "gold-api.com",
                    "raw_price": data.get("price"),
                    "buy_price": None,
                    "sell_price": None,
                    "mid_price": mid_usd,
                    "mid_price_cny_g": mid_cny,
                    "official_usd_cny": official_usd_cny,
                    "raw_text_excerpt": "",
                    "raw_payload_excerpt": str(data)[:500],
                    "fetch_mode": mode,
                    "source_url": fallback_url,
                    "error_reason": "fallback API has mid price only",
                    "note": "fallback API provides mid price only; buy/sell are unavailable",
                }
                return {
                    "london_gold_buy_usd_oz": _failed_with_previous(
                        "london_gold_buy_usd_oz",
                        previous.get("london_gold_buy_usd_oz") if previous else None,
                        {**debug, "error_reason": "fallback API has mid price only"},
                    ),
                    "london_gold_sell_usd_oz": _failed_with_previous(
                        "london_gold_sell_usd_oz",
                        previous.get("london_gold_sell_usd_oz") if previous else None,
                        {**debug, "error_reason": "fallback API has mid price only"},
                    ),
                    "london_gold_mid_usd_oz": _normal("london_gold_mid_usd_oz", mid_usd, debug, source_url=fallback_url),
                    "london_gold_buy_cny_g": _failed_with_previous(
                        "london_gold_buy_cny_g",
                        previous.get("london_gold_buy_cny_g") if previous else None,
                        {**debug, "error_reason": "fallback API has mid price only"},
                    ),
                    "london_gold_sell_cny_g": _failed_with_previous(
                        "london_gold_sell_cny_g",
                        previous.get("london_gold_sell_cny_g") if previous else None,
                        {**debug, "error_reason": "fallback API has mid price only"},
                    ),
                    "london_gold_mid_cny_g": _normal("london_gold_mid_cny_g", mid_cny, debug, source_url=fallback_url),
                }
            except Exception as fallback_exc:
                errors.append(f"gold-api: {fallback_exc}")
                return _failed_group(
                    keys,
                    previous,
                    {
                        "error": "; ".join(errors),
                        "error_reason": "wfbullion row not found and Gold-API fallback failed",
                        "buy_price": None,
                        "sell_price": None,
                        "mid_price": None,
                        "fallback_used": False,
                        "raw_text_excerpt": _excerpt_after_label(text, "倫敦金", 500) if text else "",
                        "fetch_mode": mode,
                        "source_url": fallback_url,
                    },
                )

    buy_usd, sell_usd = values[0], values[1]
    mid_usd = (buy_usd + sell_usd) / 2
    buy_cny = buy_usd * official_usd_cny / TROY_OUNCE_GRAMS
    sell_cny = sell_usd * official_usd_cny / TROY_OUNCE_GRAMS
    mid_cny = mid_usd * official_usd_cny / TROY_OUNCE_GRAMS
    debug = {
        "buy_usd_oz": buy_usd,
        "sell_usd_oz": sell_usd,
        "buy_price": buy_usd,
        "sell_price": sell_usd,
        "mid_price": mid_usd,
        "fallback_used": False,
        "error_reason": None,
        "official_usd_cny": official_usd_cny,
        "raw_prices": values[:4],
        "parsed_row": "倫敦金",
        "raw_text_excerpt": _excerpt_after_label(text, "倫敦金"),
        "fetch_mode": mode,
        "source_url": url,
    }
    return {
        "london_gold_buy_usd_oz": _normal("london_gold_buy_usd_oz", buy_usd, debug, source_url=url),
        "london_gold_sell_usd_oz": _normal("london_gold_sell_usd_oz", sell_usd, debug, source_url=url),
        "london_gold_mid_usd_oz": _normal("london_gold_mid_usd_oz", mid_usd, debug, source_url=url),
        "london_gold_buy_cny_g": _normal("london_gold_buy_cny_g", buy_cny, debug, source_url=url),
        "london_gold_sell_cny_g": _normal("london_gold_sell_cny_g", sell_cny, debug, source_url=url),
        "london_gold_mid_cny_g": _normal("london_gold_mid_cny_g", mid_cny, debug, source_url=url),
    }


def _normalize_okx_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r\n", " ").replace("\n", " ").replace("\t", " ")).strip()


def parse_okx_cny_prices_from_text(text: str) -> dict[str, Any]:
    normalized = _normalize_okx_text(text)
    section = normalized
    section_detected = "body_text"
    header_markers = [
        "\u5546\u5bb6\u5831\u50f9",
        "\u5546\u5bb6\u62a5\u4ef7",
        "\u8cb7 USDT",
        "\u4e70 USDT",
        "\u5546\u5bb6 \u55ae\u50f9 \u6578\u91cf/\u9650\u984d \u652f\u4ed8\u65b9\u5f0f",
        "\u5546\u5bb6 \u5355\u4ef7 \u6570\u91cf/\u9650\u989d \u652f\u4ed8\u65b9\u5f0f",
        "\u5546\u5bb6 \u55ae\u50f9",
        "\u5546\u5bb6 \u5355\u4ef7",
        "\u55ae\u50f9 \u6578\u91cf",
        "\u5355\u4ef7 \u6570\u91cf",
    ]
    beginner_markers = ["\u65b0\u624b\u53cb\u597d\u59d4\u8a17\u55ae", "\u65b0\u624b\u53cb\u597d\u59d4\u6258\u5355"]
    header_positions = [normalized.find(marker) for marker in header_markers if normalized.find(marker) >= 0]
    if header_positions:
        start = min(header_positions)
        section = normalized[start:]
        section_detected = "general_c2c_list"
    else:
        beginner_positions = [normalized.find(marker) for marker in beginner_markers if normalized.find(marker) >= 0]
        if beginner_positions:
            start = max(beginner_positions)
            next_merchant = normalized.find("\u5546\u5bb6", start + 1)
            if next_merchant >= 0:
                section = normalized[next_merchant:]
                section_detected = "after_beginner_merchant_section"
            else:
                section_detected = "beginner_section_not_used"

    parser_patterns: list[tuple[str, re.Pattern[str]]] = [
        ("price_before_cny", re.compile(r"(?<!\d)(6\.\d{2,4})\s*CNY", re.I)),
        ("cny_before_price", re.compile(r"CNY\s*(6\.\d{2,4})(?!\d)", re.I)),
        ("buy_usdt_near_price", re.compile(r"(?:\u8cb7\s*USDT|\u4e70\s*USDT|USDT).{0,40}?(6\.\d{2,4})(?!\d)", re.I)),
        ("merchant_quote_near_price", re.compile(r"(?:\u5546\u5bb6\u5831\u50f9|\u5546\u5bb6\u62a5\u4ef7|\u5831\u50f9|\u62a5\u4ef7).{0,40}?(6\.\d{2,4})(?!\d)")),
        ("unit_price_label", re.compile(r"\u55ae\u50f9\s*(6\.\d{2,4})(?!\d)|\u5355\u4ef7\s*(6\.\d{2,4})(?!\d)")),
        ("price_label", re.compile(r"\u50f9\u683c\s*(6\.\d{2,4})(?!\d)|\u4ef7\u683c\s*(6\.\d{2,4})(?!\d)")),
        ("price_near_cny_forward", re.compile(r"(?<!\d)(6\.\d{2,4})(?!\d).{0,30}?CNY", re.I)),
        ("price_near_cny_backward", re.compile(r"CNY.{0,30}?(6\.\d{2,4})(?!\d)", re.I)),
        ("price_near_usdt_forward", re.compile(r"(?<!\d)(6\.\d{2,4})(?!\d).{0,30}?USDT", re.I)),
        ("price_near_usdt_backward", re.compile(r"USDT.{0,30}?(6\.\d{2,4})(?!\d)", re.I)),
    ]
    raw_prices: list[float] = []
    parser_patterns_used: list[str] = []
    seen_spans: set[tuple[int, int]] = set()
    for pattern_name, pattern in parser_patterns:
        for match in pattern.finditer(section):
            price_text = next((group for group in match.groups() if group), None)
            if not price_text:
                continue
            try:
                price = float(price_text)
            except ValueError:
                continue
            if not 6.0 <= price <= 8.0:
                continue
            span = match.span()
            if span in seen_spans:
                continue
            raw_prices.append(price)
            seen_spans.add(span)
            if pattern_name not in parser_patterns_used:
                parser_patterns_used.append(pattern_name)
            if len(raw_prices) >= 5:
                break
        if len(raw_prices) >= 5:
            break

    return {
        "raw_prices": raw_prices[:5],
        "sample_count": len(raw_prices[:5]),
        "parser_patterns_used": parser_patterns_used,
        "normalized_text_excerpt": section[:3000],
        "section_detected": section_detected,
    }


async def _okx_page_text_and_html(page) -> tuple[str, str, list[str]]:
    errors: list[str] = []
    body_text = ""
    html = ""
    try:
        body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception as exc:
        errors.append(f"document.body.innerText: {exc}")
    try:
        html = await page.content()
    except Exception as exc:
        errors.append(f"page.content: {exc}")
    if not body_text and html:
        body_text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    return body_text or "", html or "", errors


def _okx_debug_flags(text: str, html: str) -> dict[str, bool]:
    combined = f"{text or ''} {html or ''}"
    lowered = combined.lower()
    return {
        "contains_cny": "CNY" in combined,
        "contains_usdt": "USDT" in combined,
        "contains_6xx_price": bool(re.search(r"(?<!\d)6\.\d{2,4}(?!\d)", combined)),
        "contains_captcha": "captcha" in lowered or "\u9a57\u8b49\u78bc" in combined or "\u9a8c\u8bc1\u7801" in combined,
        "contains_verify": "verify" in lowered or "\u9a57\u8b49" in combined or "\u9a8c\u8bc1" in combined,
        "contains_security": "security" in lowered or "\u5b89\u5168" in combined,
        "contains_cloudflare": "cloudflare" in lowered,
    }


def _okx_region_or_navigation_only(text: str, parsed: dict[str, Any]) -> bool:
    normalized = parsed.get("normalized_text_excerpt", "") or _normalize_okx_text(text)
    lowered = normalized.lower()
    region_blocked = "looks like you're in the united states" in lowered or "switch to the united states site" in lowered
    navigation_only = parsed.get("section_detected") == "body_text" and not parsed.get("raw_prices")
    c2c_absent = not any(marker in normalized for marker in ["\u5546\u5bb6 \u55ae\u50f9", "\u5546\u5bb6 \u5355\u4ef7", "\u5546\u5bb6\u5831\u50f9", "\u5546\u5bb6\u62a5\u4ef7"])
    return region_blocked or (navigation_only and c2c_absent)


async def fetch_okx(previous: Quote | None = None) -> Quote:
    key = "okx_cny_usdt"
    url = "https://www.okx.com/zh-hant/p2p-block/cny/buy-usdt"
    if os.getenv("OKX_PLAYWRIGHT_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return _failed_with_previous(key, previous, {"fetch_mode": "disabled", "source_url": url})
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        return _failed_with_previous(key, previous, {"fetch_mode": "playwright_unavailable", "error": str(exc), "source_url": url})

    text = ""
    html = ""
    section_text = ""
    selector_used = "body visible text regex"
    page_text_errors: list[str] = []
    page_url_after_goto = None
    page_title = None
    page = None
    try:
        async with _PLAYWRIGHT_LOCK:
            page = await _new_low_memory_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page_url_after_goto = page.url
            try:
                page_title = await page.title()
            except Exception:
                page_title = None
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(1000)
            text, html, page_text_errors = await _okx_page_text_and_html(page)
            section_text = text
            selector_used = "body visible text regex"
            for selector in [
                "[data-testid*='p2p']",
                ".p2p-market",
                ".p2p-offer",
                "main",
            ]:
                try:
                    locator = page.locator(selector).first
                    if await locator.count():
                        try:
                            candidate = await locator.inner_text(timeout=2000)
                        except Exception:
                            candidate = ""
                        if "USDT" in candidate and re.search(r"(?<!\d)(6\.\d{2,4})(?!\d)", candidate):
                            section_text = candidate
                            selector_used = selector
                            break
                except Exception:
                    continue
            await page.close()
            page = None
            await _close_shared_playwright(0)
        parse_text = section_text or text or BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        parsed = parse_okx_cny_prices_from_text(parse_text)
        if _okx_region_or_navigation_only(parse_text, parsed):
            raise ValueError("OKX quote table unavailable in Render region; page shows region/navigation content only")
        raw_prices: list[float] = parsed["raw_prices"]
        if len(raw_prices) < 3:
            raise ValueError("no visible CNY seller prices found")
        section_detected = parsed["section_detected"]
        suspicious = section_detected not in {"general_c2c_list", "after_beginner_merchant_section"} or selector_used == "body visible text regex"
        debug = {
            "sample_count": len(raw_prices),
            "raw_prices": raw_prices,
            "fetch_mode": "playwright",
            "source_url": url,
            "page_url_after_goto": page_url_after_goto,
            "page_title": page_title,
            "raw_text_excerpt": parse_text[:1000],
            "body_text_length": len(text or ""),
            "html_length": len(html or ""),
            "html_excerpt": (html or "")[:3000],
            "normalized_text_excerpt": parsed["normalized_text_excerpt"],
            "selector_used": selector_used,
            "section_detected": section_detected,
            "parser_patterns_used": parsed["parser_patterns_used"],
            "error_reason": "; ".join(page_text_errors) if page_text_errors else None,
            **_okx_debug_flags(parse_text, html),
            "validation": {
                "url_contains_buy_usdt": "buy-usdt" in url,
                "contains_usdt": "USDT" in parse_text,
                "contains_cny": "CNY" in parse_text,
                "price_method": "average first 5 normalized text prices near CNY/USDT context",
                "minimum_sample_count": 3,
            },
        }
        quote_value = mean(raw_prices)
        if suspicious:
            return _suspicious(key, quote_value, {**debug, "error_reason": "OKX prices came from broad body text rather than a confirmed C2C list selector"}, source_url=url)
        return _normal(key, quote_value, debug, source_url=url)
    except Exception as exc:
        try:
            if page:
                await page.close()
                await _close_shared_playwright(0)
        except Exception:
            pass
        if section_text or text or html:
            parse_text = section_text or text or BeautifulSoup(html, "lxml").get_text(" ", strip=True)
            parsed = parse_okx_cny_prices_from_text(parse_text)
            raw_prices = parsed["raw_prices"]
            okx_region_or_navigation_only = _okx_region_or_navigation_only(parse_text, parsed)
            if len(raw_prices) >= 3:
                if okx_region_or_navigation_only:
                    raw_prices = []
                else:
                    debug = {
                        "sample_count": parsed["sample_count"],
                        "raw_prices": raw_prices,
                        "fetch_mode": "playwright",
                        "source_url": url,
                        "page_url_after_goto": page_url_after_goto,
                        "page_title": page_title,
                        "raw_text_excerpt": parse_text[:1200],
                        "body_text_length": len(text or ""),
                        "html_length": len(html or ""),
                        "html_excerpt": (html or "")[:3000],
                        "normalized_text_excerpt": parsed["normalized_text_excerpt"],
                        "selector_used": selector_used,
                        "section_detected": parsed["section_detected"],
                        "parser_patterns_used": parsed["parser_patterns_used"],
                        "error_reason": "; ".join(page_text_errors) if page_text_errors else None,
                        "playwright_error": str(exc),
                        **_okx_debug_flags(parse_text, html),
                        "validation": {
                            "url_contains_buy_usdt": "buy-usdt" in url,
                            "contains_usdt": "USDT" in parse_text,
                            "contains_cny": "CNY" in parse_text,
                            "price_method": "average first 5 normalized text prices near CNY/USDT context after Playwright error",
                            "minimum_sample_count": 3,
                        },
                    }
                    return _normal(key, mean(raw_prices[:5]), debug, source_url=url)
            error_reason = (
                "OKX quote table unavailable in Render region; page shows region/navigation content only"
                if okx_region_or_navigation_only
                else f"fewer than 3 qualified OKX CNY/USDT prices found after Playwright error: {exc}"
            )
            return _failed_with_previous(
                key,
                previous,
                {
                    "sample_count": 0 if okx_region_or_navigation_only else parsed["sample_count"],
                    "raw_prices": [] if okx_region_or_navigation_only else raw_prices,
                    "fetch_mode": "playwright",
                    "source_url": url,
                    "page_url_after_goto": page_url_after_goto,
                    "page_title": page_title,
                    "raw_text_excerpt": parse_text[:1200],
                    "body_text_length": len(text or ""),
                    "html_length": len(html or ""),
                    "html_excerpt": (html or "")[:3000],
                    "normalized_text_excerpt": parsed["normalized_text_excerpt"],
                    "selector_used": selector_used,
                    "section_detected": parsed["section_detected"],
                    "parser_patterns_used": parsed["parser_patterns_used"],
                    "error": str(exc),
                    "error_reason": error_reason,
                    **_okx_debug_flags(parse_text, html),
                },
            )
        return _failed_with_previous(
            key,
            previous,
            {
                "sample_count": 0,
                "raw_prices": [],
                "fetch_mode": "playwright",
                "source_url": url,
                "page_url_after_goto": page_url_after_goto,
                "page_title": page_title,
                "body_text_length": len(text or ""),
                "html_length": len(html or ""),
                "html_excerpt": (html or "")[:3000],
                "normalized_text_excerpt": "",
                "error": str(exc),
                "error_reason": "OKX Playwright text unavailable or parser found fewer than 3 prices",
                **_okx_debug_flags(text, html),
            },
        )
