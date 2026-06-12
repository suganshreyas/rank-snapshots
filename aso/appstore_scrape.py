"""Scrape the App Store web page for data the iTunes API omits.

iTunes Search/Lookup returns EMPTY screenshotUrls for some apps (notably big or
video-led listings like PictureThis), and never returns the subtitle. Both live
in the page's `serialized-server-data` JSON. This is the fallback source.

FRAGILE: depends on Apple's page structure (script id + shelfMapping path).
Monitor — if it returns nothing for a known-good app, Apple changed the page.
"""
import html as _html
import json
import re
from typing import Any, Dict, List, Optional

import httpx

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")
_SCRIPT_RE = re.compile(
    r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', re.S)
# Subtitle renders as <p class="...subtitle...">text</p> (class hash varies per build)
_SUBTITLE_RE = re.compile(r'<p class="[^"]*subtitle[^"]*">([^<]+)</p>')


def _build(template: str, w: int = 393, h: int = 852) -> str:
    return (template.replace("{w}", str(w)).replace("{h}", str(h))
            .replace("{c}", "bb").replace("{f}", "webp"))


async def scrape_app_page(
    track_view_url: str, client: Optional[httpx.AsyncClient] = None
) -> Dict[str, Any]:
    """Return {'subtitle': str, 'screenshots': [url], 'ipad_screenshots': [url]}.

    Empty dict on any failure (caller falls back gracefully).
    """
    if not track_view_url:
        return {}
    should_close = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        r = await client.get(track_view_url, headers={"User-Agent": _UA})
        if r.status_code != 200:
            return {}
        sub_m = _SUBTITLE_RE.search(r.text)
        subtitle = _html.unescape(sub_m.group(1).strip()) if sub_m else ""
        m = _SCRIPT_RE.search(r.text)
        if not m:
            return {"subtitle": subtitle}
        blob = json.loads(m.group(1))
        node = blob["data"][0]["data"]
        shelf = node.get("shelfMapping", {})

        def shots(key: str) -> List[str]:
            out = []
            for it in shelf.get(key, {}).get("items", []):
                art = it.get("screenshot") or {}
                t = art.get("template")
                if t:
                    out.append(_build(t, art.get("width", 393) and 393,
                                      art.get("height", 852) and 852))
            return out

        return {
            "subtitle": subtitle or node.get("subtitle") or "",
            "screenshots": shots("product_media_phone_"),
            "ipad_screenshots": shots("product_media_pad_"),
        }
    except Exception:
        return {}
    finally:
        if should_close:
            await client.aclose()
