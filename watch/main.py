"""Entrypoint: `python -m watch.main`. Builds the shared Context (relay
client, alerter, store, config) and registers phase-1's four checks against
the Engine scheduler, then runs forever.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from watch import store
from watch.alerting import DiscordAlerter
from watch.checks import flat_by_close, killswitch_dryfire, ops_monitor_runner, stop_coverage
from watch.config import WatchSettings, load_watch_yaml
from watch.engine import CheckSpec, Engine
from watch.relay_client import RelayClient
from watch.reports import daily_trade_report, eod, mid_session, premarket, trade_validation, weekly

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("watch.main")


@dataclass
class Context:
    settings: WatchSettings
    relay: RelayClient
    alerter: DiscordAlerter
    db: object  # sqlite3.Connection
    watch_cfg: dict


def build_context() -> Context:
    settings = WatchSettings()
    watch_cfg = load_watch_yaml(settings.watch_config_path)
    alerting_cfg = watch_cfg.get("alerting", {})
    return Context(
        settings=settings,
        relay=RelayClient(settings.jd_relay_url, settings.jd_relay_secret),
        alerter=DiscordAlerter(
            settings.discord_bot_token, settings.discord_ops_channel_id,
            base_dedupe_window_seconds=alerting_cfg.get("base_dedupe_window_seconds", 300),
            max_dedupe_window_seconds=alerting_cfg.get("max_dedupe_window_seconds", 7200),
        ),
        db=store.connect(settings.store_path),
        watch_cfg=watch_cfg,
    )


def build_checks(ctx: Context) -> list[CheckSpec]:
    cfg = ctx.watch_cfg
    return [
        CheckSpec(
            name=stop_coverage.NAME, run_fn=stop_coverage.run,
            interval_seconds=cfg.get("stop_coverage", {}).get("poll_interval_seconds", 60),
        ),
        CheckSpec(
            name=flat_by_close.NAME, run_fn=flat_by_close.run,
            # Interval, not daily_at: the check itself no-ops before
            # check_time_et and re-alerts on an escalating cadence after it
            # (see flat_by_close.py's run() docstring) -- it needs to be
            # invoked repeatedly through the post-close window, not once.
            interval_seconds=cfg.get("flat_by_close", {}).get("realert_interval_seconds", 300),
        ),
        CheckSpec(
            name=killswitch_dryfire.NAME, run_fn=killswitch_dryfire.run,
            daily_at_et=cfg.get("killswitch_dryfire", {}).get("run_time_et", "08:30"),
        ),
        CheckSpec(
            name=ops_monitor_runner.NAME, run_fn=ops_monitor_runner.run,
            interval_seconds=cfg.get("ops_monitor_runner", {}).get("interval_seconds_rth", 1800),
        ),
        CheckSpec(
            name=premarket.NAME, run_fn=premarket.run,
            daily_at_et=cfg.get(premarket.NAME, {}).get("run_time_et", "08:45"),
        ),
        CheckSpec(
            name=mid_session.NAME, run_fn=mid_session.run,
            # Interval, not daily_at: the report itself no-ops outside RTH
            # (see mid_session.py's run()) -- needs to be invoked
            # repeatedly through the session, not once.
            interval_seconds=cfg.get(mid_session.NAME, {}).get("interval_seconds", 3600),
        ),
        CheckSpec(
            name=eod.NAME, run_fn=eod.run,
            daily_at_et=cfg.get(eod.NAME, {}).get("run_time_et", "16:15"),
        ),
        CheckSpec(
            name=daily_trade_report.NAME, run_fn=daily_trade_report.run,
            # Right after eod_report -- incident/reconciliation summary
            # first, then the P&L breakdown, matching the natural reading
            # order of "what happened, then what did it cost."
            daily_at_et=cfg.get(daily_trade_report.NAME, {}).get("run_time_et", "16:16"),
        ),
        CheckSpec(
            name=weekly.NAME, run_fn=weekly.run,
            # daily_at_et, same as premarket/eod: CheckSpec's once-per-ET-
            # date semantics already give "runs once, after this time, each
            # day it's due" -- weekly.py's own internal Friday gate (see its
            # run()) turns that into "once per week" without needing new
            # scheduling machinery.
            daily_at_et=cfg.get(weekly.NAME, {}).get("run_time_et", "16:20"),
        ),
        CheckSpec(
            name=trade_validation.NAME, run_fn=trade_validation.run,
            # Right after weekly_digest, same daily_at_et + internal-Friday-
            # gate pattern -- see trade_validation.py's own run() docstring.
            daily_at_et=cfg.get(trade_validation.NAME, {}).get("run_time_et", "16:25"),
        ),
    ]


async def main() -> None:
    ctx = build_context()
    checks = build_checks(ctx)
    log.info("jd_watch_starting checks=%s", [c.name for c in checks])
    engine = Engine(ctx, checks)
    await engine.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
