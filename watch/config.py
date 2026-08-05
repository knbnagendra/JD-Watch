"""JD-Watch settings: env vars only (never hardcoded), matching JD-Relay's
jd_relay/config.py Secrets pattern (pydantic-settings BaseSettings reading
.env, field names lowercase snake_case auto-mapped case-insensitively from
env var names) -- house style across both existing repos, not something to
reinvent here as raw os.environ.get() calls.

watch.yaml (per-check intervals/thresholds) is loaded separately via
load_watch_yaml() below -- config vs. secrets stays split the same way
JD-Relay splits config.yaml (non-secret) from .env (Secrets, credentials
only), rather than mixing thresholds into env vars or secrets into yaml.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class WatchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # JD-Relay's webhook is bound to 127.0.0.1:8787 with no public HTTP --
    # JD-Watch must run on the same VM (see README.md's deployment section).
    jd_relay_url: str = "http://127.0.0.1:8787"
    jd_relay_secret: str = ""

    discord_bot_token: str = ""
    discord_ops_channel_id: str = ""

    # Sibling-directory convention already used by JD-Signal's ops_monitor.py
    # (JD_RELAY_DIR = JD_SIGNAL_DIR.parent / "JD-Relay") -- JD-Watch's
    # ops_monitor_runner check needs the JD-Signal repo path and the python
    # interpreter (its venv) to invoke ops_monitor.py as a subprocess.
    jd_signal_repo_path: str = ""
    jd_signal_python: str = ""

    # Same pattern, JD-Relay side -- daily_trade_report.py invokes
    # JD-Relay's own trade_report.py as a subprocess (its own venv/deps,
    # not JD-Watch's).
    jd_relay_repo_path: str = ""
    jd_relay_python: str = ""

    store_path: str = "jd_watch.db"
    watch_config_path: str = "watch.yaml"


def load_watch_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
