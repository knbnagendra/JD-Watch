"""Manual CLI to log a real bug fix: `python -m watch.log_bug --repo jd-relay
--description "..." --commit <sha>`. One deliberate human record per real
bug, feeding weekly_digest's "days since last logged bug fix" / "bugs fixed
this week" section (store.py's bug_log table) -- not inferred from commit
messages or incident resolution, since neither reliably distinguishes "a
real bug" from routine work.
"""

from __future__ import annotations

import argparse

from watch import store
from watch.config import WatchSettings


def main() -> None:
    parser = argparse.ArgumentParser(description="Log a real bug fix for the weekly digest")
    parser.add_argument("--repo", required=True, help="e.g. jd-relay, jd-signal, jd-watch")
    parser.add_argument("--description", required=True)
    parser.add_argument("--commit", default="", help="commit sha, if already committed")
    args = parser.parse_args()

    settings = WatchSettings()
    conn = store.connect(settings.store_path)
    try:
        store.record_bug(conn, args.repo, args.description, args.commit)
    finally:
        conn.close()

    print(f"Logged [{args.repo}] {args.description}" + (f" ({args.commit})" if args.commit else ""))


if __name__ == "__main__":
    main()
