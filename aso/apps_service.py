"""F2 — top-10 ranking apps for a keyword, per market, with full metadata.

Two-step: iTunes search gives rank order (validated to match Astro top-7), then
iTunes lookup fills full metadata + screenshots (search alone often omits
screenshotUrls). All free, no auth.
"""
import asyncio
from typing import Any, Dict, List, Optional

import httpx

from aso.markets import itunes_country
from aso.appstore_scrape import scrape_app_page
from utils.itunes_api import search_apps, lookup_apps


def _app_view(rank: int, raw: Dict[str, Any]) -> Dict[str, Any]:
    """Shape one iTunes result into the fields the UI needs."""
    return {
        "rank": rank,
        "adam_id": str(raw.get("trackId", "")),
        "name": raw.get("trackName", ""),
        "subtitle": raw.get("subtitle") or "",
        "developer": raw.get("artistName", ""),
        "icon_url": raw.get("artworkUrl512") or raw.get("artworkUrl100") or "",
        "screenshots": raw.get("screenshotUrls", []) or [],
        "ipad_screenshots": raw.get("ipadScreenshotUrls", []) or [],
        "rating": raw.get("averageUserRating"),
        "rating_count": raw.get("userRatingCount", 0) or 0,
        "price": raw.get("formattedPrice", "Free"),
        "version": raw.get("version", ""),
        "last_updated": raw.get("currentVersionReleaseDate", ""),
        "release_notes": raw.get("releaseNotes", ""),
        "genre": raw.get("primaryGenreName", ""),
        "url": raw.get("trackViewUrl", ""),
    }


async def get_keyword_top10(
    keyword: str,
    storefront: str,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """Return the top-10 apps for a keyword in one market, rank-ordered."""
    country = itunes_country(storefront)
    should_close = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        ranked = await search_apps(keyword, country=country, limit=10, client=client)
        ranked = ranked[:10]
        ids = [str(r.get("trackId", "")) for r in ranked if r.get("trackId")]
        # lookup fills screenshots/release notes that search omits
        detailed = await lookup_apps(ids, country=country, client=client)
        by_id = {str(d.get("trackId", "")): d for d in detailed}
        out = []
        for i, r in enumerate(ranked, 1):
            adam = str(r.get("trackId", ""))
            merged = {**r, **by_id.get(adam, {})}  # lookup wins where present
            out.append(_app_view(i, merged))

        # Fallback: scrape the App Store page for apps iTunes returned 0 shots for
        # (and to fill missing subtitles). Only for the gaps — keeps it fast.
        gaps = [a for a in out
                if not a["screenshots"] or not a["ipad_screenshots"] or not a["subtitle"]]
        if gaps:
            scraped = await asyncio.gather(
                *[scrape_app_page(a["url"], client) for a in gaps]
            )
            for a, s in zip(gaps, scraped):
                if not a["screenshots"] and s.get("screenshots"):
                    a["screenshots"] = s["screenshots"]
                if not a["ipad_screenshots"] and s.get("ipad_screenshots"):
                    a["ipad_screenshots"] = s["ipad_screenshots"]
                if not a["subtitle"] and s.get("subtitle"):
                    a["subtitle"] = s["subtitle"]
        return out
    finally:
        if should_close:
            await client.aclose()
