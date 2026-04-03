"""Odoo-specific neutralization: disable crons and queue jobs for dev environments."""

from __future__ import annotations

import re

from ...core.postgres import PgConfig


def neutralize(pg: PgConfig, db_name: str, callback=None) -> dict:
    """Neutralize Odoo runtime artifacts for dev use.

    - Marks pending queue_job records as done
    - Deactivates all ir.cron records
    - Runs VACUUM ANALYZE
    """
    summary = {"errors_found": 0, "fixes_applied": 0, "details": []}

    def _log(msg: str):
        if callback:
            callback(msg)

    # ── Neutralize queue jobs ─────────────────────────────────────
    _log("   Neutralizando queue jobs pendientes...")
    result = pg.run_psql(
        db_name,
        """UPDATE queue_job SET state = 'done'
        WHERE state IN ('pending', 'enqueued', 'started', 'failed');""",
    )
    if result.returncode == 0:
        count = _extract_update_count(result.stdout)
        if count > 0:
            summary["fixes_applied"] += count
            summary["details"].append(f"queue_job neutralizados: {count}")

    # ── Deactivate crons ──────────────────────────────────────────
    _log("   Desactivando crons...")
    result = pg.run_psql(db_name, "UPDATE ir_cron SET active = false;")
    if result.returncode == 0:
        count = _extract_update_count(result.stdout)
        if count > 0:
            summary["fixes_applied"] += count
            summary["details"].append(f"ir.cron desactivados: {count}")

    # ── VACUUM ────────────────────────────────────────────────────
    _log("   VACUUM ANALYZE...")
    pg.run_psql(db_name, "VACUUM ANALYZE;")

    return summary


def _extract_update_count(stdout: str) -> int:
    match = re.search(r"UPDATE (\d+)", stdout)
    return int(match.group(1)) if match else 0
