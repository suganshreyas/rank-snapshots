# rank-snapshots

Daily snapshots of iTunes Search rankings (top-10 apps per keyword, per
storefront) plus listing metadata, stored in Supabase. The keyword set lives
in the database, not in this repo.

- **Source:** the public, unauthenticated iTunes Search/Lookup API, paced at
  one keyword every 2.5s with retry/backoff. A page scrape fills in subtitles
  and screenshots the API omits.
- **Schedule:** one GitHub Actions matrix job per storefront, daily at 05:00 UTC.
- **Idempotent + resumable:** pairs already snapshotted today are skipped, so
  re-running a failed job only does the missing work.
- **Logs are counts-only by design** — no keyword names appear in public logs.

## Setup

1. Create the Supabase tables (see the consuming project's migrations:
   `tracked_keywords`, `rank_snapshots`, `app_snapshots`, `app_changes`).
2. Add repo secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
3. The workflow runs daily; trigger manually via *Actions → Daily rank
   snapshots → Run workflow*.

## Local use

```bash
pip install -r requirements.txt
export SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
python runner.py --dry-run            # counts only
python runner.py --storefront US      # one market
```
