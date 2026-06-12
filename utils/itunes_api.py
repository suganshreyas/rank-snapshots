"""iTunes Search API, Lookup API, and Search Hints client."""
import asyncio
import httpx
from typing import List, Dict, Any, Optional

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
SEARCH_HINTS_URL = "https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"

# Rate limit: ~20 req/min per IP for iTunes APIs
RATE_LIMIT_DELAY = 1.0  # seconds between requests


async def search_apps(
    term: str,
    country: str = "us",
    limit: int = 50,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """Search iTunes for apps matching a term. Returns raw result dicts."""
    params = {
        "term": term,
        "country": country,
        "media": "software",
        "limit": min(limit, 200),
    }
    should_close = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        resp = await client.get(SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    finally:
        if should_close:
            await client.aclose()


async def lookup_apps(
    app_ids: List[str],
    country: str = "us",
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """Batch lookup apps by IDs (up to 200 per call)."""
    if not app_ids:
        return []

    should_close = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        results = []
        # iTunes allows up to 200 IDs per lookup call
        for i in range(0, len(app_ids), 200):
            batch = app_ids[i:i + 200]
            ids_str = ",".join(str(aid) for aid in batch)
            params = {"id": ids_str, "country": country}
            resp = await client.get(LOOKUP_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            if i + 200 < len(app_ids):
                await asyncio.sleep(RATE_LIMIT_DELAY)
        return results
    finally:
        if should_close:
            await client.aclose()


async def search_hints(
    term: str,
    client: Optional[httpx.AsyncClient] = None,
) -> List[str]:
    """Get search hint suggestions from Apple.

    Response is plist XML. Each hint is a dict with 'term' and 'url'.
    Requires User-Agent and X-Apple-Store-Front headers.
    """
    import plistlib

    params = {"media": "software", "term": term}
    headers = {
        "User-Agent": "iTunes/12.0",
        "X-Apple-Store-Front": "143441-1,29",  # US store
    }
    should_close = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=True)
    try:
        resp = await client.get(SEARCH_HINTS_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = plistlib.loads(resp.content)
        # Each hint is {"term": "...", "url": "..."} — extract just the terms
        hints = data.get("hints", [])
        return [h["term"] if isinstance(h, dict) else h for h in hints]
    except Exception:
        return []
    finally:
        if should_close:
            await client.aclose()


def parse_app_metadata(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Parse raw iTunes API result into our standard metadata format."""
    # Determine price model
    price = raw.get("price", 0) or 0
    has_iap = len(raw.get("ipadScreenshotUrls", [])) > 0  # rough proxy

    if price > 0:
        price_model = f"${price:.2f}"
    elif raw.get("isGameCenterEnabled"):
        price_model = "Free"
    else:
        price_model = "Free"

    return {
        "app_id": str(raw.get("trackId", "")),
        "app_name": raw.get("trackName", ""),
        "bundle_id": raw.get("bundleId"),
        "developer_name": raw.get("artistName"),
        "artist_id": str(raw.get("artistId", "")),
        "price": price,
        "currency": raw.get("currency", "USD"),
        "formatted_price": raw.get("formattedPrice", "Free"),
        "avg_rating": raw.get("averageUserRating"),
        "rating_count": raw.get("userRatingCount", 0) or 0,
        "current_version_rating_count": raw.get("userRatingCountForCurrentVersion"),
        "version": raw.get("version"),
        "current_version_release_date": raw.get("currentVersionReleaseDate"),
        "release_date": raw.get("releaseDate"),
        "primary_genre": raw.get("primaryGenreName"),
        "genre_id": str(raw.get("primaryGenreId", "")),
        "description": raw.get("description"),
        "subtitle": raw.get("subtitle", ""),
        "file_size_bytes": int(raw.get("fileSizeBytes", 0) or 0),
        "minimum_os_version": raw.get("minimumOsVersion"),
        "content_rating": raw.get("contentAdvisoryRating"),
        "icon_url": raw.get("artworkUrl100"),
        "track_view_url": raw.get("trackViewUrl", ""),
    }


async def lookup_developer_apps(
    artist_id: str,
    country: str = "us",
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """Lookup all apps by a developer via their artist ID.

    Returns list of app dicts (name, id) for the developer's portfolio.
    """
    if not artist_id:
        return []

    should_close = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        params = {"id": artist_id, "entity": "software", "country": country}
        resp = await client.get(LOOKUP_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        # First result is the artist itself, rest are apps
        apps = [
            r for r in data.get("results", [])
            if r.get("wrapperType") == "software"
        ]
        return apps
    except Exception:
        return []
    finally:
        if should_close:
            await client.aclose()
