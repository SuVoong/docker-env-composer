"""Generic plugin: minimal behavior for databases without a specific ERP.

Only does dump/restore + PG template. No schema sync, no module install,
no ERP-specific neutralization.
"""

from __future__ import annotations

from ..core.docker import docker_exec
from ..core.postgres import PgConfig
from .base import ERPPlugin


class GenericPlugin(ERPPlugin):
    """Fallback plugin with no ERP-specific behavior."""

    @property
    def name(self) -> str:
        return "generic"

    @staticmethod
    def detect_from_compose(services: dict) -> dict | None:
        """Generic always matches as fallback (returns minimal env)."""
        # Find any postgres service
        pg_svc = None
        app_svc = None
        for name, svc in services.items():
            image = svc.get("image", "")
            if "postgres" in image.lower():
                pg_svc = (name, svc)
            elif not app_svc:
                app_svc = (name, svc)

        if not pg_svc:
            return None

        pg_name, pg_cfg = pg_svc
        app_name = app_svc[0] if app_svc else pg_name

        pg_port = 5432
        for port_map in pg_cfg.get("ports", []):
            port_str = str(port_map)
            if ":" in port_str:
                pg_port = int(port_str.split(":")[0])
                break

        pg_env = pg_cfg.get("environment", {})
        if isinstance(pg_env, list):
            env_dict = {}
            for item in pg_env:
                if "=" in str(item):
                    k, _, v = str(item).partition("=")
                    env_dict[k] = v
            pg_env = env_dict

        container = (app_svc[1] if app_svc else pg_cfg).get("container_name", app_name)

        return {
            "erp_plugin": "generic",
            "erp_version": "n/a",
            "odoo_container": container,  # backward compat key
            "pg_container": pg_cfg.get("container_name", pg_name),
            "pg_port": pg_port,
            "pg_user": pg_env.get("POSTGRES_USER", "postgres"),
            "pg_password": pg_env.get("POSTGRES_PASSWORD", "postgres"),
        }

    def post_restore(
        self,
        container: str,
        db_name: str,
        pg: PgConfig,
        callback=None,
    ) -> dict:
        """No post-restore steps for generic databases."""
        if callback:
            callback("   No ERP-specific post-restore steps (generic plugin)")
        return {"fixes_applied": 0, "details": []}

    def install_modules(
        self,
        container: str,
        db_name: str,
        modules: list[str],
    ) -> None:
        """Not supported for generic databases."""
        raise NotImplementedError(
            "Module installation not supported by the generic plugin. "
            "Use an ERP-specific plugin (e.g. odoo)."
        )

    def restore_filestore(
        self,
        container: str,
        db_name: str,
        src_dir: str,
    ) -> None:
        """No-op: generic plugin doesn't manage filestores."""
        pass

    def remove_filestore(self, container: str, db_name: str) -> None:
        """No-op: generic plugin doesn't manage filestores."""
        pass

    def copy_filestore(
        self,
        container: str,
        src_db: str,
        dst_db: str,
    ) -> None:
        """No-op: generic plugin doesn't manage filestores."""
        pass

    def neutralize(
        self,
        pg: PgConfig,
        db_name: str,
        callback=None,
    ) -> dict:
        """Only VACUUM for generic databases."""
        if callback:
            callback("   VACUUM ANALYZE...")
        pg.run_psql(db_name, "VACUUM ANALYZE;")
        return {"fixes_applied": 0, "details": []}
