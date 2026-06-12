"""Daily rank + listing-metadata snapshots for a tracked keyword set.

Reads the (keyword, storefront) pairs to snapshot from a Supabase table and
stores the day's top-10 rankings + app metadata back into Supabase. Designed
to run on a schedule (GitHub Actions); idempotent per day and resumable —
pairs already snapshotted today are skipped, so re-runs only do missing work.

PRIVACY NOTE: this repo's Action logs are public, so this script logs COUNTS
AND INDICES ONLY — never keyword names. Keep it that way.

Usage:
  python runner.py                      # all tracked pairs
  python runner.py --storefront US      # one market (used by the CI matrix)
  python runner.py --prune              # also prune old metadata snapshots
  python runner.py --dry-run            # show counts, fetch nothing
  python runner.py --limit 3            # only N pairs (testing)
"""
import argparse
import asyncio
from datetime import datetime, timezone

import httpx

from aso.snapshot_store import (
    snapshot_keyword, list_tracked, snapshots_done_today, prune_app_snapshots,
)

DELAY_SECONDS = 2.5  # between keyword snapshots — keep iTunes happy


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


async def _snapshot_with_retry(kw, sf, client, attempts=3):
    """Retry with backoff — handles iTunes 429s AND transient network errors
    (connection reset, read timeout) that otherwise kill a long run."""
    for attempt in range(1, attempts + 1):
        try:
            return await snapshot_keyword(kw, sf, client=client)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 403, 503) and attempt < attempts:
                wait = 5 * (2 ** (attempt - 1))  # 5s, 10s, 20s
                _log(f"   HTTP {e.response.status_code}, retry in {wait}s")
                await asyncio.sleep(wait)
                continue
            raise
        except httpx.RequestError as e:  # ReadError, ConnectError, timeouts
            if attempt < attempts:
                wait = 3 * attempt
                _log(f"   network {type(e).__name__}, retry in {wait}s")
                await asyncio.sleep(wait)
                continue
            raise


async def run(pairs, label: str) -> int:
    done = snapshots_done_today()
    todo = [(k, s) for k, s in pairs if (k, s) not in done]
    _log(f"{label}: {len(pairs)} pairs total, {len(pairs) - len(todo)} already "
         f"done today, {len(todo)} to fetch")
    ok = fail = 0
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for i, (kw, sf) in enumerate(todo, 1):
            try:
                await _snapshot_with_retry(kw, sf, client)
                ok += 1
            except Exception as e:
                fail += 1
                _log(f"  pair {i}/{len(todo)} [{sf}] FAILED: {type(e).__name__}")
            if i % 50 == 0:
                _log(f"  progress {i}/{len(todo)} ({fail} failed)")
            await asyncio.sleep(DELAY_SECONDS)
    _log(f"DONE — {ok} ok, {fail} failed, {len(pairs) - len(todo)} skipped")
    return fail


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--storefront", help="only this market (e.g. US) — used by the CI matrix")
    p.add_argument("--prune", action="store_true",
                   help="prune bulky metadata snapshots older than 14 days (run in ONE matrix job)")
    p.add_argument("--dry-run", action="store_true", help="show counts, fetch nothing")
    p.add_argument("--limit", type=int, help="only snapshot N pairs (testing)")
    args = p.parse_args()

    pairs = [(t["keyword"], t["storefront"]) for t in list_tracked()]
    if args.storefront:
        sf = args.storefront.strip().upper()
        pairs = [(k, s) for k, s in pairs if s == sf]
    if args.limit:
        pairs = pairs[: args.limit]
    label = f"Snapshot run ({args.storefront.upper() if args.storefront else 'all markets'})"

    if args.dry_run:
        done = snapshots_done_today()
        todo = [(k, s) for k, s in pairs if (k, s) not in done]
        _log(f"DRY RUN — {label}: {len(pairs)} pairs, {len(todo)} would be fetched")
        return

    fail = asyncio.run(run(pairs, label))

    if args.prune:
        try:
            pruned = prune_app_snapshots(days=14)
            _log(f"Pruned {pruned} metadata snapshots older than 14 days.")
        except Exception as e:
            _log(f"prune skipped: {type(e).__name__}")

    if fail and fail > len(pairs) * 0.2:  # fail the job if >20% errored
        raise SystemExit(1)


if __name__ == "__main__":
    main()
