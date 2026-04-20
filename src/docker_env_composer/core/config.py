"""Global configuration for dec (~/.dec/config.json).

Stores known Docker environments and the last used one for instant loading.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".dec"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_config() -> dict:
    if CONFIG_FILE.is_file():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def _save_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2, default=str))


# ── Known environments ────────────────────────────────────────────


def get_known_envs() -> list[dict]:
    """Return saved environments (instant, no filesystem scan).

    Each entry: {compose_dir, display_name, erp_plugin, erp_version, ...}
    """
    cfg = _load_config()
    return cfg.get("known_envs", [])


def add_known_env(env: dict) -> None:
    """Save an environment to the known list (dedup by compose_dir)."""
    cfg = _load_config()
    envs = cfg.get("known_envs", [])

    # Keep only serializable keys
    entry = {
        "compose_dir": env["compose_dir"],
        "compose_file": env.get("compose_file", ""),
        "display_name": env.get("display_name", ""),
        "erp_plugin": env.get("erp_plugin", "generic"),
        "erp_version": env.get("erp_version", "?"),
        "odoo_container": env.get("odoo_container", ""),
        "pg_container": env.get("pg_container", ""),
        "pg_port": env.get("pg_port", 5432),
        "pg_user": env.get("pg_user", ""),
        "pg_password": env.get("pg_password", ""),
        "templates_dir": env.get("templates_dir", ""),
    }

    # Replace if same compose_dir already exists
    envs = [e for e in envs if e["compose_dir"] != entry["compose_dir"]]
    envs.append(entry)

    cfg["known_envs"] = envs
    _save_config(cfg)


def remove_known_env(compose_dir: str) -> None:
    """Remove an environment from the known list."""
    cfg = _load_config()
    envs = cfg.get("known_envs", [])
    cfg["known_envs"] = [e for e in envs if e["compose_dir"] != compose_dir]
    _save_config(cfg)


# ── Last used ─────────────────────────────────────────────────────


def get_last_env_path() -> str | None:
    """Return the compose directory path of the last used environment."""
    cfg = _load_config()
    return cfg.get("last_env_path")


def set_last_env_path(compose_dir: str) -> None:
    """Save the compose directory path as the last used environment."""
    cfg = _load_config()
    cfg["last_env_path"] = compose_dir
    _save_config(cfg)
