"""Odoo ERP plugin for docker-env-composer.

Provides Odoo-specific behavior: schema sync, odoo-bin, filestore management,
queue_job/ir_cron neutralization.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from ...core.docker import docker_cp_to, docker_exec
from ...core.postgres import PgConfig
from ...core.sanitize import validate_container_name, validate_db_name, validate_module_list
from ...core.utils import _elapsed
from ..base import ERPPlugin
from . import filestore as _filestore
from . import neutralize as _neutralize
from .schema_sync import SCHEMA_SYNC_SCRIPT


class OdooPlugin(ERPPlugin):
    """Odoo ERP plugin."""

    @property
    def name(self) -> str:
        return "odoo"

    @staticmethod
    def detect_from_compose(services: dict) -> dict | None:
        """Detect Odoo from docker-compose services by image name."""
        odoo_svc = None
        pg_svc = None
        for name, svc in services.items():
            image = svc.get("image", "")
            if "odoo" in image.lower():
                odoo_svc = (name, svc)
            elif "postgres" in image.lower():
                pg_svc = (name, svc)

        if not odoo_svc or not pg_svc:
            return None

        odoo_name, odoo_cfg = odoo_svc
        pg_name, pg_cfg = pg_svc

        # Extract version from image tag
        odoo_image = odoo_cfg.get("image", "")
        version_match = re.search(r"(\d+\.\d+)", odoo_image)
        erp_version = version_match.group(1) if version_match else "unknown"

        # Container names (validated to prevent injection via compose file)
        odoo_container = validate_container_name(
            odoo_cfg.get("container_name", odoo_name)
        )
        pg_container = validate_container_name(
            pg_cfg.get("container_name", pg_name)
        )

        # PG port
        pg_port = 5432
        for port_map in pg_cfg.get("ports", []):
            port_str = str(port_map)
            if ":" in port_str:
                pg_port = int(port_str.split(":")[0])
                break

        # PG credentials
        pg_env = _parse_env(pg_cfg.get("environment", []))
        pg_user = pg_env.get("POSTGRES_USER", "odoo")
        pg_password = pg_env.get("POSTGRES_PASSWORD", "odoo")

        # Odoo HTTP port
        odoo_port = 8069
        for port_map in odoo_cfg.get("ports", []):
            port_str = str(port_map)
            if ":" in port_str:
                parts = port_str.split(":")
                if int(parts[-1]) == 8069:
                    odoo_port = int(parts[0])
                    break

        return {
            "erp_plugin": "odoo",
            "erp_version": erp_version,
            "odoo_container": odoo_container,
            "pg_container": pg_container,
            "pg_port": pg_port,
            "pg_user": pg_user,
            "pg_password": pg_password,
            "odoo_port": odoo_port,
        }

    def post_restore(
        self,
        container: str,
        db_name: str,
        pg: PgConfig,
        callback=None,
    ) -> dict:
        """Run odoo-bin -u base + schema sync after dump restore."""
        validate_db_name(db_name)
        summary = {"fixes_applied": 0, "details": []}

        def _log(msg: str):
            if callback:
                callback(msg)

        # ── Step A: odoo-bin -u base ──────────────────────────────
        _log(f"\n{'─' * 55}")
        _log("odoo-bin -u base (sincroniza esquema core + registro)...")
        _log("   Ejecutando dentro del contenedor (puede tardar varios minutos)...")
        t_step = time.time()
        sync_result = docker_exec(
            container,
            [
                "python3",
                "/mnt/environment/odoo-server/odoo-bin",
                "-c", "/mnt/environment/confs/odoo.conf",
                "-d", db_name,
                "-u", "base",
                "--stop-after-init",
                "--no-http",
            ],
            timeout=900,
        )
        if sync_result.returncode == 0:
            summary["details"].append("odoo-bin -u base: OK")
            _log(f"   ✔ odoo-bin -u base: OK [{_elapsed(t_step)}]")
        else:
            summary["details"].append(
                f"odoo-bin -u base: parcial (rc={sync_result.returncode})"
            )
            _log(f"   ⚠ odoo-bin -u base: parcial (rc={sync_result.returncode}) [{_elapsed(t_step)}]")
            if sync_result.stderr:
                err_lines = [l for l in sync_result.stderr.split("\n") if l.strip() and "WARNING" not in l]
                for line in err_lines[-3:]:
                    _log(f"   {line.strip()[:120]}")

        # ── Step B: Schema sync (5 phases) ────────────────────────
        _log(f"\n{'─' * 55}")
        _log("Schema sync + limpieza de artefactos huérfanos...")
        _log("     → Phase 1: columnas faltantes (AST scan)")
        _log("     → Phase 2: vistas de módulos ausentes")
        _log("     → Phase 3: vistas con campos inexistentes")
        _log("     → Phase 4: modelos huérfanos (ir.model, access, rules)")
        _log("     → Phase 5: columnas espurias (limpieza cartesian-product)")

        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
            tmp.write(SCHEMA_SYNC_SCRIPT)
            tmp_path = tmp.name

        _log("   Copiando script de sync al contenedor...")
        docker_cp_to(container, tmp_path, "/tmp/dec_schema_sync.py")

        _log("   Ejecutando schema sync dentro del contenedor...")
        t_sync = time.time()
        result = docker_exec(
            container,
            [
                "python3", "/tmp/dec_schema_sync.py",
                "/mnt/environment/confs/odoo.conf",
                db_name,
            ],
            timeout=600,
        )

        Path(tmp_path).unlink(missing_ok=True)

        if result.returncode == 0:
            stdout = result.stdout or ""
            for line in stdout.split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("+"):
                    summary["fixes_applied"] += 1
                if "Cleaned" in stripped:
                    summary["details"].append(stripped)
                if "DROPPED" in stripped:
                    summary["details"].append(stripped)
                    summary["fixes_applied"] += 1
                _log(f"   {stripped}")
            _log(f"   ✔ Schema sync completado [{_elapsed(t_sync)}]")
        else:
            _log(f"   ⚠ Schema sync parcial (rc={result.returncode}) [{_elapsed(t_sync)}]")
            if result.stdout:
                for line in result.stdout.split("\n")[-15:]:
                    if line.strip():
                        _log(f"   {line.strip()}")
            if result.stderr:
                for line in result.stderr.split("\n")[-5:]:
                    if line.strip():
                        _log(f"   ERROR: {line.strip()}")

        return summary

    def install_modules(
        self,
        container: str,
        db_name: str,
        modules: list[str],
    ) -> None:
        """Install Odoo modules via odoo-bin -i."""
        validate_db_name(db_name)
        validate_module_list(modules)
        docker_exec(
            container,
            [
                "python3",
                "/mnt/environment/odoo-server/odoo-bin",
                "-c", "/mnt/environment/confs/odoo.conf",
                "-d", db_name,
                "-i", ",".join(modules),
                "--stop-after-init",
                "--no-http",
            ],
            timeout=1200,
        )

    def restore_filestore(
        self,
        container: str,
        db_name: str,
        src_dir: str,
    ) -> None:
        """Restore Odoo filestore from extracted ZIP."""
        _filestore.restore(container, db_name, src_dir)

    def remove_filestore(self, container: str, db_name: str) -> None:
        """Remove Odoo filestore for a database."""
        _filestore.remove(container, db_name)

    def copy_filestore(
        self,
        container: str,
        src_db: str,
        dst_db: str,
    ) -> None:
        """Copy Odoo filestore between databases."""
        _filestore.copy(container, src_db, dst_db)

    def neutralize(
        self,
        pg: PgConfig,
        db_name: str,
        callback=None,
    ) -> dict:
        """Neutralize Odoo runtime artifacts (queue_job, ir_cron)."""
        return _neutralize.neutralize(pg, db_name, callback)


def _parse_env(env_list: list | dict) -> dict:
    """Parse Docker environment variables from list or dict format."""
    if isinstance(env_list, dict):
        return env_list
    result = {}
    for item in env_list:
        if "=" in str(item):
            key, _, value = str(item).partition("=")
            result[key] = value
    return result
