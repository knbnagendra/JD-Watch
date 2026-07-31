# JD-Watch

Autonomous, out-of-band ops monitoring for the JD trading stack (JD-Signal +
JD-Relay). Read-only by default; the one exception is calling JD-Relay's
existing kill switch (`POST /control/halt`) to halt new entries on an
affected account. **JD-Watch never flattens a position and never resumes
trading on its own** -- every incident it raises ends in a human decision,
via the exact resume command in [RUNBOOK.md](RUNBOOK.md).

## Why this exists (phase 1 scope)

JD-Signal and JD-Relay already have mature monitoring for most of what a
"full ops monitor" would normally need to build from scratch: regime-state
tracking, signal cooldown/dedup, per-account kill-switch state, broker-
position reconciliation, and Discord alerting with exponential backoff.
Phase 1 of JD-Watch deliberately covers only the confirmed real gaps left
after that inventory, not a duplicate reporting layer over data that
already exists elsewhere:

1. **`stop_coverage`** -- a periodic, external sweep confirming every open
   position still has a live protective stop. JD-Relay's own protection-
   failure handling is reactive (triggered only when a specific chunk's
   protective order dies); this is the standing, independent check of the
   whole book.
2. **`flat_by_close`** -- a standalone "are we actually flat by close"
   assertion, independent of JD-Relay's own EOD sweep, with escalating
   re-alerts if a flatten gets stuck.
3. **`killswitch_dryfire`** -- a daily pre-market self-test of the halt/
   rearm mechanism itself, against sandbox accounts only.
4. **`ops_monitor_runner`** -- runs JD-Signal's existing `ops_monitor.py`
   CLI on a schedule instead of requiring a human to remember to invoke it.

Everything else in the original, broader ops-monitoring spec (data
integrity, regime-behavior reporting, most of broker/account state,
infra metrics) either already exists in one of the two repos or is mostly
a reporting layer over data that already does -- deferred to a later phase
once phase 1 has a live track record.

## Architecture

```
watch/
  main.py          entrypoint: python -m watch.main
  engine.py         scheduler (interval or once-per-ET-date checks)
  config.py         .env settings (pydantic-settings) + watch.yaml loader
  severity.py        INFO / WARN / CRITICAL / HALT
  store.py            sqlite: check_results, incidents (persisted so a
                      restart doesn't forget an in-progress incident)
  relay_client.py      thin HTTP client for JD-Relay's control surface
  alerting.py           Discord posting + exponential backoff dedupe
  market_hours.py        vendored from JD-Relay (stdlib only, decoupled)
  checks/
    stop_coverage.py
    flat_by_close.py
    killswitch_dryfire.py
    ops_monitor_runner.py
watch.yaml           every interval/threshold, no magic numbers in code
```

JD-Watch talks to JD-Relay purely over HTTP (`GET /positions`,
`GET /control/status`, `POST /control/halt`, `POST /control/rearm`) --
never by importing `jd_relay` as a package. This keeps the two processes
fully decoupled: a crashed JD-Relay must not crash JD-Watch, and a crashed
JD-Watch must never be able to stop trading (it can only ever halt *new*
entries via the one sanctioned endpoint, on request).

## Deployment

JD-Relay's webhook is bound to `127.0.0.1:8787` with no public HTTP (VM
firewall allows only inbound `22/tcp`) -- **JD-Watch must run on the same
VM** as `jd-relay.service`/`jd-signal.service`, as a third systemd service.
See `deploy/jd-watch.service`.

```
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in JD_RELAY_SECRET, DISCORD_BOT_TOKEN, etc.
sudo cp deploy/jd-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jd-watch
```

## Adding a new check

1. Add a module under `watch/checks/` with a `NAME` constant and a
   `run(ctx) -> None` function. `ctx` carries `.relay` (RelayClient),
   `.alerter` (DiscordAlerter), `.db` (sqlite3.Connection), `.watch_cfg`
   (dict from `watch.yaml`), `.settings` (WatchSettings). Use
   `store.open_incident`/`close_incident`/`should_realert` for anything
   that needs to persist across restarts or escalate on a re-alert
   interval; use `ctx.alerter.alert(key, severity, content)` /
   `.resolve(key)` for the actual Discord post.
2. Register it in `watch/main.py`'s `build_checks()` as a `CheckSpec`
   (either `interval_seconds=...` or `daily_at_et="HH:MM"`).
3. Add its thresholds to `watch.yaml` under a section named after the
   check.

## Testing

```
venv/bin/pytest
```

No test may make a real network call (`tests/conftest.py`'s `no_network`
fixture fails any test that tries) -- use `FakeRelayClient`/`FakeAlerter`
from `tests/conftest.py`.

## Hard safety constraint

Every auto-action in this repo only ever halts new entries on one account
-- never flattens, never resumes. `killswitch_dryfire`'s target list is
hardcoded in code (`watch/checks/killswitch_dryfire.py`'s `TARGETS`), never
read from config, so a config typo can never point a dry-fire test at a
live account.
