"""Dashboard: list all databases with metadata + integrated drop."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.events import Key
from textual.widgets import DataTable, Footer, Header, Static

from ...core.detect import get_plugin_for_env
from ...core.postgres import PgConfig, database_size, drop_database, list_databases
from ...core.registry import get_database, list_registered, unregister_database
from ..banner import BANNER, BANNER_SUBTITLE

CSS_PATH = Path(__file__).parent.parent / "styles.tcss"


class DashboardApp(App):
    """Database dashboard with table view and integrated drop."""

    CSS_PATH = CSS_PATH
    TITLE = "dec — Docker Env Composer"
    BINDINGS = [
        Binding("q", "quit", "Salir"),
        Binding("r", "refresh", "Refrescar"),
        Binding("d", "request_drop", "Eliminar"),
        Binding("escape", "cancel_drop", "Cancelar", show=False),
    ]

    def __init__(self, env: dict) -> None:
        super().__init__()
        self.env = env
        self._db_names: list[str] = []
        self._pending_drop: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(BANNER, id="banner")
        yield Static(BANNER_SUBTITLE, id="banner-subtitle")
        yield Static("", id="status-bar")
        yield DataTable(id="db-table")
        yield Footer()

    def on_mount(self) -> None:
        self._load_table()

    def _load_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.add_columns(
            "Nombre", "Descripción", "Versión", "Template", "Creada", "Expira", "Tamaño"
        )

        pg = PgConfig.from_env(self.env)

        try:
            real_dbs = set(list_databases(pg))
        except RuntimeError:
            real_dbs = set()

        registered = {db["name"]: db for db in list_registered()}
        all_names = sorted(real_dbs | set(registered.keys()))
        self._db_names = []

        for name in all_names:
            reg = registered.get(name)
            exists = name in real_dbs

            if not exists:
                continue

            if reg:
                desc = reg.get("description", "")
                version = reg.get("erp_version", reg.get("odoo_version", "?"))
                template = reg.get("template") or "─"
                created = reg.get("created_at", "")[:10]
                expires = reg.get("expires_at", "")
                if expires:
                    exp_date = datetime.fromisoformat(expires)
                    now = datetime.now()
                    if now > exp_date:
                        expires = f"⚠ {expires[:10]}"
                    else:
                        days_left = (exp_date - now).days
                        expires = f"{expires[:10]} ({days_left}d)"
                else:
                    expires = "─"
            else:
                desc = "(sin registrar)"
                version = self.env.get("erp_version", "?")
                template = "─"
                created = "─"
                expires = "─"

            try:
                size = database_size(pg, name)
            except Exception:
                size = "?"

            table.add_row(name, desc, version, template, created, expires, size)
            self._db_names.append(name)

        self._update_status()

    def _get_selected_db(self) -> str | None:
        """Get the database name at the current cursor row."""
        table = self.query_one(DataTable)
        if table.row_count == 0 or not self._db_names:
            return None
        row_idx = table.cursor_coordinate.row
        if 0 <= row_idx < len(self._db_names):
            return self._db_names[row_idx]
        return None

    def _update_status(self, msg: str | None = None) -> None:
        """Update the status bar."""
        status = self.query_one("#status-bar", Static)
        if msg:
            status.update(f"  {msg}")
        else:
            plugin_name = self.env.get("erp_plugin", "generic")
            version = self.env.get("erp_version", "?")
            container = self.env.get("odoo_container", "?")
            odoo_ok = "✔" if self.env.get("odoo_running") else "✘"
            pg_ok = "✔" if self.env.get("pg_running") else "✘"
            count = len(self._db_names)
            status.update(
                f"DB: {count}  │  {plugin_name} {version}  │  {container} {odoo_ok}  │  PG: {self.env.get('pg_port', '?')} {pg_ok}"
            )

    # ── Actions (Binding) + on_key fallback ─────────────────────

    def action_request_drop(self) -> None:
        """Called by Binding('d'). Delegates to _handle_drop."""
        self._handle_drop()

    def action_cancel_drop(self) -> None:
        """Called by Binding('escape')."""
        if self._pending_drop:
            self._pending_drop = None
            self._update_status()

    def on_key(self, event: Key) -> None:
        """Fallback: catch d/Esc if Binding didn't fire (DataTable focus)."""
        if event.key == "d":
            event.prevent_default()
            self._handle_drop()
        elif event.key == "escape" and self._pending_drop:
            event.prevent_default()
            self._pending_drop = None
            self._update_status()

    def _handle_drop(self) -> None:
        """First d: mark for deletion. Second d: confirm and drop."""
        db_name = self._get_selected_db()
        if not db_name:
            return

        if self._pending_drop == db_name:
            self._execute_drop(db_name)
        else:
            self._pending_drop = db_name
            self._update_status(
                f"[bold #e04040]⚠ ¿Eliminar '{db_name}'?[/]  "
                f"Pulsa [bold]d[/] de nuevo para confirmar  │  [bold]Esc[/] cancelar"
            )

    def _flash_status(self, msg: str, seconds: float = 2.0) -> None:
        """Show a temporary message, then restore default status."""
        self._update_status(msg)
        self.set_timer(seconds, self._update_status)

    def _execute_drop(self, db_name: str) -> None:
        """Actually drop the database, filestore, and registry entry."""
        self._pending_drop = None
        pg = PgConfig.from_env(self.env)

        try:
            self._update_status(f"[#f0b429]Eliminando '{db_name}'...[/]")
            self.refresh()
            drop_database(pg, db_name)
            plugin = get_plugin_for_env(self.env)
            plugin.remove_filestore(self.env.get("odoo_container", ""), db_name)
            unregister_database(db_name)
            self._load_table()
            self._flash_status(f"[#1ed760]✔ '{db_name}' eliminada correctamente[/]")
        except Exception as e:
            self._flash_status(f"[#e04040]✘ Error eliminando '{db_name}': {e}[/]", 5.0)

    def action_refresh(self) -> None:
        self._pending_drop = None
        self._load_table()

    def action_create(self) -> None:
        self.exit(return_code=10)
