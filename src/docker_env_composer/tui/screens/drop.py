"""Drop selector: interactive database deletion."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static

from ...core.detect import get_plugin_for_env
from ...core.postgres import PgConfig, database_size, drop_database, list_databases
from ...core.registry import list_registered, unregister_database
from ..banner import BANNER

CSS_PATH = Path(__file__).parent.parent / "styles.tcss"


class DropSelectorApp(App):
    """Interactive selector to drop databases."""

    CSS_PATH = CSS_PATH
    TITLE = "dec — Eliminar entorno"
    BINDINGS = [
        Binding("q", "quit", "Salir"),
        Binding("escape", "quit", "Salir"),
        Binding("d", "confirm_drop", "✘ Eliminar"),
    ]

    def __init__(self, env: dict) -> None:
        super().__init__()
        self.env = env
        self._db_names: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "  ⚠ Selecciona una base de datos y pulsa [bold]d[/] para eliminar",
            id="drop-title",
        )
        yield DataTable(id="db-table")
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._load_table()

    def _load_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.add_columns("Nombre", "Descripción", "Creada", "Expira", "Tamaño")

        pg = PgConfig.from_env(self.env)
        try:
            real_dbs = list_databases(pg)
        except RuntimeError:
            real_dbs = []

        registered = {db["name"]: db for db in list_registered()}

        self._db_names = sorted(real_dbs)
        for name in self._db_names:
            reg = registered.get(name)
            desc = reg.get("description", "") if reg else "(sin registrar)"
            created = reg.get("created_at", "")[:10] if reg else "-"
            expires = reg.get("expires_at", "")[:10] if reg and reg.get("expires_at") else "-"

            try:
                size = database_size(pg, name)
            except Exception:
                size = "?"

            table.add_row(name, desc, created, expires, size)

    def action_confirm_drop(self) -> None:
        table = self.query_one(DataTable)
        status = self.query_one("#status-bar", Static)

        if table.row_count == 0 or not self._db_names:
            return

        # Get the row index from cursor position
        row_idx = table.cursor_coordinate.row
        if row_idx < 0 or row_idx >= len(self._db_names):
            return

        db_name = self._db_names[row_idx]
        pg = PgConfig.from_env(self.env)

        try:
            drop_database(pg, db_name)
            plugin = get_plugin_for_env(self.env)
            plugin.remove_filestore(self.env["odoo_container"], db_name)
            unregister_database(db_name)
            status.update(f"  [green]'{db_name}' eliminada.[/]")
            self._load_table()
        except Exception as e:
            status.update(f"  [red]Error eliminando '{db_name}': {e}[/]")
