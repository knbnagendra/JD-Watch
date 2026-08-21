# Trade Performance Validation

A recurring, dated log of real trade-performance data pulled directly from
JD-Relay's own journals (`jd_relay_<account>.db`), cross-checked for data-
quality issues before the numbers are trusted for anything -- including,
eventually, external use. Each entry below is a point-in-time snapshot,
never silently updated in place; corrections and re-checks are appended,
not edited into the original.

**This is not a marketing document.** It exists so that by the time there
*is* a real track record worth showing anyone, there's a paper trail
proving the numbers were validated along the way, not assembled after the
fact. See "Data quality & methodology" below before reading any number in
this file at face value.

---

## 2026-08-08 -- baseline snapshot

Pulled directly from each account's `closed_trades` table, joined to
`alerts` for product attribution. Query is reproducible -- see
"Methodology" below.

| Account | Kind | n closed | Total P&L | Win rate | % exits approximate |
|---|---|---:|---:|---:|---:|
| `tradier_sandbox` | paper | 43 | **+$1,261.50** | 44% | 5% (2/43) |
| `alpaca_sandbox` | paper | 11 | **-$2,354.67** | 9% | **100% (11/11)** |
| `tradier_live` | live | 1 | -$94.00 | 0% | 0% (0/1) |
| `alpaca_live` | live | 0 | -- | -- | -- |
| `schwab_live` | live | 0 | -- | -- | -- |

### Per-product breakdown (paper accounts only -- the only ones with enough volume to break down)

| Account | Product | n | P&L | Win rate | % approx |
|---|---|---:|---:|---:|---:|
| tradier_sandbox | fuse | 15 | +$1,243.50 | 60% | 0% |
| tradier_sandbox | sentinel | 27 | +$30.00 | 37% | 4% |
| tradier_sandbox | swing | 1 | -$12.00 | 0% | 100% |
| alpaca_sandbox | sentinel | 5 | -$1,535.00 | 0% | **100%** |
| alpaca_sandbox | beacon | 6 | -$819.67 | 17% | **100%** |

### Real finding from this snapshot: Alpaca exit-price bug

Every single non-EOD-flatten exit ever recorded on `alpaca_sandbox` (11/11,
across both products it trades) was marked `_approx` -- meaning the system
never found a real fill price in Alpaca's response and silently substituted
the *intended* stop/target price instead. Root cause: `_leg_fill_price`/
`is_approximate` in `jd_relay/position_manager.py` only recognized
Tradier's fill-price field names (`avg_fill_price`, `last_fill_price`,
`price`); Alpaca's real field is `filled_avg_price`, which was never in the
list. Confirmed via `broker/alpaca.py`'s own module docstring: only the
entry leg's *shape* (which leg is which) was ever validated against a real
Alpaca fill; no exit leg's price field was.

**Fixed and deployed same day** (JD-Relay commit `280cff3`, both `main` and
`prod`, regression-tested fail-before/pass-after). Going forward, new
Alpaca exits will record the broker's real fill price. **The `alpaca_sandbox`
P&L figures above are the numbers as originally recorded, before this fix
-- they are not restated retroactively, and are not to be treated as
accurate** (real slippage on those 11 trades is unknown; the recorded
numbers used the intended stop/target price, not what the broker actually
filled at). Whether the true number is better or worse than -$2,354.67 is
unknown without re-deriving it from raw broker order history, which hasn't
been done.

### What this snapshot does NOT support

- **No product-mapping-adjusted comparison.** `tradier_sandbox` and
  `alpaca_sandbox` don't carry the same products (`config.yaml`'s
  per-account `products:` allowlist) -- `fuse` never traded on
  `alpaca_sandbox` at all despite being in its allowed list, and `beacon`
  is structurally unavailable on `tradier_sandbox`. The -$2,354.67 vs.
  +$1,261.50 headline gap is not a clean same-strategy, same-broker
  comparison.
- **No live-account read.** `tradier_live` has one trade ever (2026-07-27,
  -$94.00). `alpaca_live` and `schwab_live` have zero closed trades --
  everything on those two accounts so far is unrealized/open-position
  exposure, not a completed track record.
- **No slippage-corrected Alpaca numbers yet** (see above).

---

## Data quality & methodology

**Query used** (adjust `db_path` per account):
```python
import sqlite3, json
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT ct.ticker, ct.exit_reason, ct.pnl_dollars, a.payload_json
    FROM closed_trades ct JOIN alerts a ON ct.alert_id = a.alert_id
    ORDER BY ct.closed_at
""").fetchall()
# product = json.loads(row['payload_json'])['product']
```
Run directly against JD-Relay's own per-account journal DBs
(`jd_relay_<account>.db`) on the VM -- these are the same databases
JD-Relay itself writes to on every fill, not a separate analytics copy.

**Known caveats to check every time this is re-run:**
1. **`exit_reason` ending in `_approx`** means the exit price is NOT the
   broker's confirmed fill -- it's a fallback (intended stop/target price,
   or a reconstruction during reconciliation). A high approx rate on any
   account/broker combination is itself a signal worth investigating
   before trusting that account's P&L, exactly as it was here.
2. **Account naming has changed once already** (`sandbox`/`live` ->
   `tradier_sandbox`/`tradier_live`, 2026-08-07/08) -- confirm the actual
   current account names in `config.yaml` before assuming a db filename.
3. **Small-sample accounts are not yet meaningful.** Anything under ~20-30
   closed trades (i.e. every live account today) shouldn't be read as a
   real win-rate/P&L signal -- noise dominates at this sample size.
4. Cross-check against the live broker's own reporting (Tradier/Alpaca/
   Schwab's own statements) periodically, not just this system's own
   journal -- the whole point of a second, independent check.

---

## 2026-08-14

**Trade Performance Validation -- since 2026-08-07T20:25:07.043228+00:00**

| Account | Kind | n | P&L | Win rate | % approx |
|---|---|---:|---:|---:|---:|
| tradier_sandbox | paper | 6 | $-82.50 | 0% | 0% |
| tradier_live | live | 4 | $-21.00 | 50% | 0% |
| alpaca_sandbox | paper | 4 | $-707.03 | 0% | 50% |
| alpaca_live | live | 0 | -- | -- | -- |
| schwab_live | live | 0 | -- | -- | -- |

Per-product breakdown:
| Account | Product | n | P&L | Win rate | % approx |
|---|---|---:|---:|---:|---:|
| tradier_sandbox | sentinel | 4 | $-78.50 | 0% | 0% |
| tradier_sandbox | fuse | 2 | $-4.00 | 0% | 0% |
| tradier_live | fuse | 2 | $11.00 | 100% | 0% |
| tradier_live | sentinel | 2 | $-32.00 | 0% | 0% |
| alpaca_sandbox | beacon | 2 | $-367.03 | 0% | 100% |
| alpaca_sandbox | sentinel | 1 | $-315.00 | 0% | 0% |
| alpaca_sandbox | fuse | 1 | $-25.00 | 0% | 0% |

**Readiness (is this data trustworthy yet?)**

Sample size (floor: 30 closed trades/product before a win-rate number is meaningful):
- tradier_live/fuse: 2/30 closed trades
- tradier_live/sentinel: 2/30 closed trades
- alpaca_live: 0 closed trades
- schwab_live: 0 closed trades

This week: NOT CLEAN (2/14 approx exits, 1 new CRITICAL incidents)
Consecutive clean weeks: 0

---

## 2026-08-21

**Trade Performance Validation -- since 2026-08-14T20:25:02.100541+00:00**

| Account | Kind | n | P&L | Win rate | % approx |
|---|---|---:|---:|---:|---:|
| tradier_sandbox | paper | 5 | $-372.50 | 20% | 20% |
| tradier_live | live | 4 | $200.00 | 50% | 25% |
| alpaca_sandbox | paper | 7 | $-305.98 | 29% | 71% |
| alpaca_live | live | 0 | -- | -- | -- |

Per-product breakdown:
| Account | Product | n | P&L | Win rate | % approx |
|---|---|---:|---:|---:|---:|
| tradier_sandbox | fuse | 3 | $-305.00 | 0% | 0% |
| tradier_sandbox | swing | 1 | $69.00 | 100% | 100% |
| tradier_sandbox | sentinel | 1 | $-136.50 | 0% | 0% |
| tradier_live | swing | 2 | $206.00 | 50% | 50% |
| tradier_live | sentinel | 2 | $-6.00 | 50% | 0% |
| alpaca_sandbox | beacon | 4 | $-382.98 | 0% | 75% |
| alpaca_sandbox | fuse | 1 | $30.00 | 100% | 0% |
| alpaca_sandbox | swing | 2 | $47.00 | 50% | 100% |

**Readiness (is this data trustworthy yet?)**

Sample size (floor: 30 closed trades/product before a win-rate number is meaningful):
- tradier_live/swing: 2/30 closed trades
- tradier_live/sentinel: 2/30 closed trades
- alpaca_live: 0 closed trades

This week: NOT CLEAN (7/16 approx exits, 3 new CRITICAL incidents)
Consecutive clean weeks: 0

---

## Next scheduled check

<!-- New dated sections are inserted automatically directly above this
heading -- watch/reports/trade_validation.py appends here every Friday
16:25 ET, right after weekly_digest. Do not remove this heading; the
report's insertion logic depends on it being present verbatim. -->

**Automated as of 2026-08-08** (JD-Watch's `trade_validation` report):
every Friday, pulls the trailing 7 days via `trade_report.py
--validation-summary`, appends a new dated section above, posts the same
content to Discord, and records that week's clean/dirty verdict (zero
approximate exits + zero new CRITICAL incidents = clean) to
`validation_weeks` in JD-Watch's own store -- the running "N consecutive
clean weeks" streak that answers "is this ready to show anyone yet" is
derived from that history, not asserted. See
`project_jd_watch_ops_monitoring_2026_07_30.md` for the readiness bar this
is measured against (~30-50 closed trades/product on each live account,
and several consecutive clean weeks).

The automation appends to this file locally on the VM, then commits and
pushes just this one file (`git add`/`commit`/`push`, scoped to this path
only) so the new section lands in this repo's real git history
unattended, via a dedicated deploy key on the VM
(`~/.ssh/deploy_jdwatch`, registered on GitHub with write access,
`origin` on the VM aliased to it as `github-jdwatch`). If a push ever
fails (network blip, deploy key rotated/revoked), the Discord post for
that week says so explicitly and the local file on the VM stays the
source of truth until the next successful run reconciles it -- see
`RUNBOOK.md`'s `trade_validation` section.
