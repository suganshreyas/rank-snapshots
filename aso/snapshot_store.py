"""Daily snapshots + change detection for the competitor movement layer (B).

Supabase-backed (hosted Postgres) so the GitHub Action and the local backend
share one store — the data accumulates daily regardless of any one machine.
Schema: supabase/migrations/004_movement_tracking.sql. The snapshot HISTORY is
the moat — it accrues forward and can't be backfilled.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from aso.apps_service import get_keyword_top10
from utils.db import get_client

# Metadata fields we diff for "what changed". screenshots compared as a set.
_META_FIELDS = ["name", "subtitle", "icon_url", "version", "price", "release_notes"]


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    """No-op — tables are created by the Supabase migration."""
    return


def register_keyword(keyword: str, storefront: str) -> None:
    """Register a (keyword, market) as tracked WITHOUT snapshotting (fast insert).

    Lets us register a whole seed list up front so every pair is tracked even if
    the snapshotting pass is interrupted — the daily Action then fills them in.
    """
    get_client().table("tracked_keywords").upsert(
        {"keyword": keyword.strip().lower(), "storefront": storefront},
        on_conflict="keyword,storefront",
    ).execute()


def register_keywords_bulk(pairs) -> None:
    """Batch-register many (keyword, storefront) pairs as tracked."""
    db = get_client()
    rows = [{"keyword": k.strip().lower(), "storefront": sf} for k, sf in pairs]
    for i in range(0, len(rows), 500):
        db.table("tracked_keywords").upsert(
            rows[i:i + 500], on_conflict="keyword,storefront").execute()


def snapshotted_today(keyword: str, storefront: str) -> bool:
    """True if this (keyword, market) already has a snapshot for today."""
    rows = (get_client().table("rank_snapshots").select("position")
            .eq("keyword", keyword.strip().lower()).eq("storefront", storefront)
            .eq("date", _today()).limit(1).execute().data)
    return bool(rows)


def snapshots_done_today() -> set:
    """Set of (keyword, storefront) already snapshotted today — one prefetch so a
    re-run (e.g. after laptop sleep) can skip done work cheaply, in memory."""
    db = get_client()
    today = _today()
    done, start = set(), 0
    while True:
        rows = (db.table("tracked_keywords")
                .select("keyword,storefront,last_snapshot_at")
                .range(start, start + 999).execute().data)
        if not rows:
            break
        for r in rows:
            if (r.get("last_snapshot_at") or "").startswith(today):
                done.add((r["keyword"], r["storefront"]))
        if len(rows) < 1000:
            break
        start += 1000
    return done


def prune_app_snapshots(days: int = 14) -> int:
    """Delete app metadata snapshots older than `days` to bound storage.

    Keeps rank_snapshots (small, the trend data) and app_changes (the 'what
    changed' history) forever; only the bulky per-day metadata is pruned.
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    res = get_client().table("app_snapshots").delete().lt("date", cutoff).execute()
    return len(res.data) if res.data else 0


async def snapshot_keyword(
    keyword: str, storefront: str, client: Optional[httpx.AsyncClient] = None
) -> Dict[str, Any]:
    """Fetch today's top-10 + metadata and store it. Idempotent per day."""
    keyword = keyword.strip().lower()
    apps = await get_keyword_top10(keyword, storefront, client=client)
    date = _today()
    db = get_client()

    db.table("tracked_keywords").upsert(
        {"keyword": keyword, "storefront": storefront, "last_snapshot_at": _now()},
        on_conflict="keyword,storefront",
    ).execute()

    if apps:
        db.table("rank_snapshots").upsert(
            [{"keyword": keyword, "storefront": storefront, "date": date,
              "position": a["rank"], "adam_id": a["adam_id"]} for a in apps],
            on_conflict="keyword,storefront,date,position",
        ).execute()
        db.table("app_snapshots").upsert(
            [{"adam_id": a["adam_id"], "storefront": storefront, "date": date,
              "data": a} for a in apps],
            on_conflict="adam_id,storefront,date",
        ).execute()

    changes = detect_changes(keyword, storefront, date)
    return {"keyword": keyword, "storefront": storefront, "date": date,
            "apps": len(apps), "changes_detected": len(changes)}


def _distinct_dates(db, keyword: str, storefront: str, limit: int = 2) -> List[str]:
    rows = (db.table("rank_snapshots").select("date")
            .eq("keyword", keyword).eq("storefront", storefront)
            .order("date", desc=True).execute().data)
    seen = []
    for r in rows:
        if r["date"] not in seen:
            seen.append(r["date"])
        if len(seen) >= limit:
            break
    return seen


def _ranks(db, keyword, storefront, date) -> Dict[str, int]:
    rows = (db.table("rank_snapshots").select("adam_id,position")
            .eq("keyword", keyword).eq("storefront", storefront).eq("date", date)
            .execute().data)
    return {r["adam_id"]: r["position"] for r in rows}


def _app(db, adam, storefront, date) -> Optional[Dict[str, Any]]:
    rows = (db.table("app_snapshots").select("data")
            .eq("adam_id", adam).eq("storefront", storefront).eq("date", date)
            .limit(1).execute().data)
    return rows[0]["data"] if rows else None


def detect_changes(keyword: str, storefront: str, date: str = None) -> List[Dict[str, Any]]:
    """Diff `date` (default today) vs the previous snapshot date. Writes app_changes."""
    date = date or _today()
    db = get_client()
    dates = _distinct_dates(db, keyword, storefront, limit=10)
    prev = next((d for d in dates if d < date), None)
    if not prev:
        return []  # day 1 — no movement

    today_ranks = _ranks(db, keyword, storefront, date)
    prev_ranks = _ranks(db, keyword, storefront, prev)
    # idempotent re-run: clear this date's diff rows first
    db.table("app_changes").delete().eq("keyword", keyword)\
        .eq("storefront", storefront).eq("date", date).execute()

    out: List[Dict[str, Any]] = []

    def add(adam, ctype, rf, rt, before, after, name):
        out.append({"adam_id": adam, "storefront": storefront, "keyword": keyword,
                    "date": date, "change_type": ctype, "rank_from": rf,
                    "rank_to": rt, "before": before, "after": after, "app_name": name})

    for adam, pos in today_ranks.items():
        app = _app(db, adam, storefront, date) or {}
        name = app.get("name", "")
        old = prev_ranks.get(adam)
        if old is None:
            add(adam, "rank_enter", None, pos, None, f"#{pos}", name)
        elif old != pos and not (old >= 8 and pos >= 8 and abs(old - pos) <= 1):
            add(adam, "rank_move", old, pos, f"#{old}", f"#{pos}", name)
        prev_app = _app(db, adam, storefront, prev)
        if app and prev_app:
            for f in _META_FIELDS:
                if (app.get(f) or "") != (prev_app.get(f) or ""):
                    add(adam, f, None, None, str(prev_app.get(f))[:200],
                        str(app.get(f))[:200], name)
            if set(app.get("screenshots", [])) != set(prev_app.get("screenshots", [])):
                add(adam, "screenshots", None, None,
                    f"{len(prev_app.get('screenshots', []))} shots",
                    f"{len(app.get('screenshots', []))} shots", name)
    for adam, old in prev_ranks.items():
        if adam not in today_ranks:
            pa = _app(db, adam, storefront, prev) or {}
            add(adam, "rank_exit", old, None, f"#{old}", "out of top 10", pa.get("name", ""))

    if out:
        db.table("app_changes").insert(out).execute()
    return out


def get_movement(keyword: str, storefront: str) -> Dict[str, Any]:
    """Current top-10 with rank deltas vs the previous snapshot + recent changes."""
    keyword = keyword.strip().lower()
    db = get_client()
    dates = _distinct_dates(db, keyword, storefront, limit=2)
    if not dates:
        return {"keyword": keyword, "storefront": storefront, "tracked": False,
                "snapshots": 0, "apps": [], "changes": []}
    latest = dates[0]
    prev = dates[1] if len(dates) > 1 else None
    prev_ranks = _ranks(db, keyword, storefront, prev) if prev else {}

    latest_ranks = _ranks(db, keyword, storefront, latest)
    # one query for all app metadata (instead of one per app)
    ids = list(latest_ranks.keys())
    snaps = (db.table("app_snapshots").select("adam_id,data")
             .in_("adam_id", ids).eq("storefront", storefront).eq("date", latest)
             .execute().data) if ids else []
    by_id = {s["adam_id"]: s["data"] for s in snaps}

    apps = []
    for adam, pos in sorted(latest_ranks.items(), key=lambda x: x[1]):
        app = dict(by_id.get(adam) or {})
        old = prev_ranks.get(adam)
        app["rank"] = pos
        app["delta"] = (old - pos) if old is not None else None
        app["is_new"] = old is None and prev is not None
        apps.append(app)

    changes = (db.table("app_changes").select("*")
               .eq("keyword", keyword).eq("storefront", storefront).eq("date", latest)
               .execute().data)
    tk = (db.table("tracked_keywords").select("first_tracked_at")
          .eq("keyword", keyword).eq("storefront", storefront).limit(1).execute().data)

    return {"keyword": keyword, "storefront": storefront, "tracked": True,
            "snapshots": len(dates), "latest_date": latest, "prev_date": prev,
            "first_tracked_at": tk[0]["first_tracked_at"] if tk else None,
            "apps": apps, "changes": changes}


_EVENT_MAP = {
    "version": "release", "release_notes": "release",
    "icon_url": "icon", "screenshots": "screenshots",
    "subtitle": "subtitle", "price": "price",
}


def app_history(adam_id: str, keyword: str, storefront: str, days: int = 90) -> Dict[str, Any]:
    """Real time-series for an app: rank (per keyword) and listing-change events
    (per app), over the last `days`. (Ratings analytics live in the reviews product.)"""
    from datetime import date, timedelta
    db = get_client()
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    rank_rows = (db.table("rank_snapshots").select("date,position")
                 .eq("keyword", keyword).eq("storefront", storefront).eq("adam_id", adam_id)
                 .gte("date", cutoff).order("date").execute().data)
    rank = [{"date": r["date"], "rank": r["position"]} for r in rank_rows]

    chg_rows = (db.table("app_changes").select("date,change_type,before,after")
                .eq("adam_id", adam_id).eq("storefront", storefront)
                .gte("date", cutoff).order("date").execute().data)
    # collapse to one event per (date, logical type), carrying before/after so the
    # UI can show exactly what changed on hover (e.g. subtitle before → after).
    seen, events = set(), []
    for c in chg_rows:
        t = _EVENT_MAP.get(c["change_type"])
        if t and (c["date"], t) not in seen:
            seen.add((c["date"], t))
            events.append({"date": c["date"], "type": t,
                           "before": c.get("before"), "after": c.get("after")})

    return {"rank": rank, "events": events}


def list_tracked() -> List[Dict[str, Any]]:
    """All tracked (keyword, storefront) pairs — paginated past Supabase's
    1000-row default page cap (the set is larger than one page)."""
    db = get_client()
    out, start = [], 0
    while True:
        rows = (db.table("tracked_keywords").select("*")
                .order("keyword").range(start, start + 999).execute().data)
        out.extend(rows)
        if len(rows) < 1000:
            break
        start += 1000
    return out


