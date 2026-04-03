"""Registry: metadata for managed databases (descriptions, TTL, creation date)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

REGISTRY_DIR = Path.home() / ".dec"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"


def _load() -> dict:
    if REGISTRY_FILE.is_file():
        return json.loads(REGISTRY_FILE.read_text())
    return {"databases": []}


def _save(data: dict) -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(data, indent=2, default=str))


def register_database(
    db_name: str,
    description: str,
    erp_version: str = "?",
    template: str | None = None,
    ttl_days: int | None = None,
    modules: list[str] | None = None,
    # Backward compat: accept odoo_version as alias
    odoo_version: str | None = None,
) -> dict:
    """Register a new database in the registry."""
    data = _load()
    now = datetime.now()

    entry = {
        "name": db_name,
        "description": description,
        "erp_version": odoo_version or erp_version,
        "template": template,
        "modules": modules or [],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=ttl_days)).isoformat() if ttl_days else None,
    }

    # Update if exists, else append
    existing = [i for i, db in enumerate(data["databases"]) if db["name"] == db_name]
    if existing:
        data["databases"][existing[0]] = entry
    else:
        data["databases"].append(entry)

    _save(data)
    return entry


def unregister_database(db_name: str) -> bool:
    """Remove a database from the registry."""
    data = _load()
    before = len(data["databases"])
    data["databases"] = [db for db in data["databases"] if db["name"] != db_name]
    _save(data)
    return len(data["databases"]) < before


def get_database(db_name: str) -> dict | None:
    """Get a single database entry."""
    data = _load()
    return next((db for db in data["databases"] if db["name"] == db_name), None)


def list_registered() -> list[dict]:
    """List all registered databases."""
    return _load()["databases"]


def get_expired() -> list[dict]:
    """Get databases that have passed their TTL."""
    now = datetime.now()
    expired = []
    for db in list_registered():
        if db.get("expires_at"):
            expires = datetime.fromisoformat(db["expires_at"])
            if now > expires:
                expired.append(db)
    return expired
