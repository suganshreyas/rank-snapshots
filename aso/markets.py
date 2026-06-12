"""Shared market constants for GapScout v2.

Popularity uses ISO storefront codes (UPPERCASE) for the Apple Ads endpoint.
iTunes Search API uses lowercase country codes. Keep both mappings here so
every module agrees on the MVP market list.
"""
from typing import Dict, List

# MVP Tier-1 markets (locked June 10, 2026).
MARKETS: List[str] = ["US", "GB", "CA", "AU", "DE", "FR"]

# Display names for the UI.
MARKET_NAMES: Dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "DE": "Germany",
    "FR": "France",
}

# Apple Ads storefronts are uppercase ISO; iTunes wants lowercase.
def itunes_country(storefront: str) -> str:
    return storefront.lower()
