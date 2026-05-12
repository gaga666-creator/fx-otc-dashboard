from __future__ import annotations

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
    if previous and previous.value is not None and previous.last_success_time:
        return Quote(
            key=key,
            value=previous.value,
            status="stale",
            source=meta["source"],
            source_url=previous.source_url or meta["source_url"],
            last_success_time=previous.last_success_time,
            debug=merged_debug,
        )
    return Quote(
        key=key,
        value=None,
        status="unavailable",
        source=meta["source"],
        source_url=meta["source_url"],
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


async def _playwright_body_text(url: str, wait_ms: int = 8000) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="zh-TW")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(wait_ms)
            return await page.locator("body").inner_text(timeout=10000)
        finally:
            await browser.close()


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
                date_match = re.search(r"(20\d{2})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})", label)
                if "中国外汇交易中心" in label and "人民币汇率中间价公告" in label and date_match:
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
    url = SOURCE_META["gold_9999_mid_cny_g"]["source_url"]
    errors: list[str] = []
    text = ""
    mode = "httpx"
    try:
        headers = {"User-Agent": "Mozilla/5.0 FX OTC Dashboard"}
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = BeautifulSoup(resp.text, "lxml").get_text("\n", strip=True)
        values = _numbers_after_label(text, "黄金9999")
        if len(values) < 2:
            raise ValueError("gold9999 row not found in static HTML")
    except Exception as exc:
        errors.append(f"httpx: {exc}")
        try:
            mode = "playwright"
            text = await _playwright_body_text(url)
            values = _numbers_after_label(text, "黄金9999")
            if len(values) < 2:
                raise ValueError("gold9999 row not found in rendered page")
        except Exception as render_exc:
            errors.append(f"playwright: {render_exc}")
            return _failed_group(
                keys,
                previous,
                {
                    "error": "; ".join(errors),
                    "error_reason": "gold9999 row not found or Playwright unavailable",
                    "raw_text_excerpt": _excerpt_after_label(text, "黄金9999", 500) if text else "",
                    "fetch_mode": mode,
                    "source_url": url,
                },
            )

    # Row order on quoteh5 is repurchase/sale/high/low. For user-facing trade flow,
    # "buy" means the customer buy/sale price, and "sell" means repurchase price.
    sell, buy = values[0], values[1]
    mid = (buy + sell) / 2
    debug = {
        "buy_price": buy,
        "sell_price": sell,
        "raw_prices": values[:4],
        "parsed_row": "黄金9999",
        "price_mapping": {
            "raw_prices[0]": "repurchase_price / user_sell_price",
            "raw_prices[1]": "sale_price / user_buy_price",
        },
        "raw_text_excerpt": _excerpt_after_label(text, "黄金9999"),
        "fetch_mode": mode,
        "source_url": url,
    }
    return {
        "gold_9999_buy_cny_g": _normal("gold_9999_buy_cny_g", buy, debug, source_url=url),
        "gold_9999_sell_cny_g": _normal("gold_9999_sell_cny_g", sell, debug, source_url=url),
        "gold_9999_mid_cny_g": _normal("gold_9999_mid_cny_g", mid, debug, source_url=url),
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
        values = _numbers_after_label(text, "伦敦金") or _numbers_after_label(text, "London Gold")
        if len(values) < 2:
            raise ValueError("london gold row not found in static HTML")
    except Exception as exc:
        errors.append(f"httpx: {exc}")
        try:
            mode = "playwright"
            text = await _playwright_body_text(url)
            values = _numbers_after_label(text, "伦敦金") or _numbers_after_label(text, "London Gold")
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
                    "provider": "gold-api.com",
                    "raw_price": data.get("price"),
                    "official_usd_cny": official_usd_cny,
                    "raw_payload_excerpt": str(data)[:500],
                    "fetch_mode": mode,
                    "source_url": fallback_url,
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
                        "raw_text_excerpt": _excerpt_after_label(text, "伦敦金", 500) if text else "",
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
        "official_usd_cny": official_usd_cny,
        "raw_prices": values[:4],
        "parsed_row": "伦敦金",
        "raw_text_excerpt": _excerpt_after_label(text, "伦敦金"),
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


async def fetch_okx(previous: Quote | None = None) -> Quote:
    key = "okx_cny_usdt"
    url = "https://www.okx.com/zh-hant/p2p-markets/cny/buy-usdt"
    if os.getenv("OKX_PLAYWRIGHT_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return _failed_with_previous(key, previous, {"fetch_mode": "disabled", "source_url": url})
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        return _failed_with_previous(key, previous, {"fetch_mode": "playwright_unavailable", "error": str(exc), "source_url": url})

    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(locale="zh-TW")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_url("**/buy-usdt", timeout=10000)
            await page.wait_for_timeout(5000)
            text = await page.locator("body").inner_text(timeout=10000)
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
                        candidate = await locator.inner_text(timeout=2000)
                        if "USDT" in candidate and re.search(r"(?<!\d)(6\.\d{2,4})(?!\d)", candidate):
                            section_text = candidate
                            selector_used = selector
                            break
                except Exception:
                    continue
            table_markers = ["商家\t單價", "商家 單價", "商家\n單價"]
            for marker in table_markers:
                marker_index = section_text.find(marker)
                if marker_index >= 0:
                    section_text = section_text[marker_index:]
                    selector_used = f"{selector_used} > merchant table marker"
                    break
            await browser.close()
        raw_prices: list[float] = []
        for match in re.finditer(r"(?<!\d)(6\.\d{2,4})(?!\d)", section_text):
            price = float(match.group(1))
            if 6.0 <= price <= 8.0:
                raw_prices.append(price)
            if len(raw_prices) >= 10:
                break
        if not raw_prices:
            raise ValueError("no visible CNY seller prices found")
        section_detected = "general_c2c_list" if ("USDT" in section_text and ("CNY" in section_text or "人民幣" in section_text)) else "unknown"
        suspicious = section_detected == "unknown" or selector_used == "body visible text regex"
        debug = {
            "sample_count": len(raw_prices),
            "raw_prices": raw_prices,
            "fetch_mode": "playwright",
            "source_url": url,
            "raw_text_excerpt": section_text[:1000],
            "selector_used": selector_used,
            "section_detected": section_detected,
            "validation": {
                "url_contains_buy_usdt": "buy-usdt" in url,
                "contains_usdt": "USDT" in section_text,
                "contains_cny": "CNY" in section_text or "人民幣" in section_text,
                "price_method": "average first 10 visible 6.x prices",
            },
        }
        quote_value = mean(raw_prices)
        if suspicious:
            return _suspicious(key, quote_value, {**debug, "error_reason": "OKX prices came from broad body text rather than a confirmed C2C list selector"}, source_url=url)
        return _normal(key, quote_value, debug, source_url=url)
    except Exception as exc:
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
        return _failed_with_previous(
            key,
            previous,
            {"sample_count": 0, "raw_prices": [], "fetch_mode": "playwright", "source_url": url, "error": str(exc)},
        )
