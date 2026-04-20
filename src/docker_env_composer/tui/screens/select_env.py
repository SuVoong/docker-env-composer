"""Environment selector: instant picker from saved environments."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from ...core.config import (
    add_known_env,
    get_known_envs,
    get_last_env_path,
    remove_known_env,
    set_last_env_path,
)
from ...core.detect import _enrich_with_status, detect_from_path
from ..banner import BANNER, BANNER_SUBTITLE

CSS_PATH = Path(__file__).parent.parent / "styles.tcss"


class SelectEnvApp(App):
    """Instant environment selector from saved config.

    - First run (no saved envs): shows input to add a path.
    - Subsequent runs: shows saved envs in a table for instant selection.
    - 'a' to add a new environment, 'x' to remove selected.
    """

    CSS_PATH = CSS_PATH
    TITLE = "dec — Seleccionar entorno"
    BINDINGS = [
        Binding("q", "quit", "Salir"),
        Binding("a", "add_env", "Añadir"),
        Binding("x", "remove_env", "Eliminar"),
        Binding("enter", "select", "Seleccionar", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._envs: list[dict] = []
        self.selected_env: dict | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(BANNER, id="banner")
        yield Static(BANNER_SUBTITLE, id="banner-subtitle")
        yield Static("", id="env-selector-hint")
        yield Static("", id="status-bar")
        yield DataTable(id="env-table")

        # Hidden add-path form (toggled by 'a')
        with Vertical(id="add-env-form"):
            yield Static("Ruta al directorio con docker-compose.yml:", id="add-env-label")
            yield Input(
                placeholder="ej: ~/WorkSpace/FL_Dockers/v16/docker",
                id="add-env-input",
            )
            with Horizontal(id="add-env-buttons"):
                yield Button("✔ Añadir", variant="primary", id="btn-add-confirm")
                yield Button("✘ Cancelar", variant="default", id="btn-add-cancel")
            yield Static("", id="add-env-status")

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#add-env-form").display = False
        self._load_table()

    def _load_table(self) -> None:
        table = self.query_one("#env-table", DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.add_columns("Nombre", "Tipo", "Versión", "Contenedor", "Ruta")

        self._envs = get_known_envs()
        hint = self.query_one("#env-selector-hint", Static)

        if not self._envs:
            hint.update("  No hay entornos guardados. Pulsa [bold]a[/] para añadir uno.")
            table.display = False
            self._update_status()
            return

        hint.update("  Selecciona un entorno Docker:")
        table.display = True

        last_path = get_last_env_path()
        highlight_row = 0
        home = str(Path.home())

        for i, env in enumerate(self._envs):
            name = env.get("display_name", "?")
            erp = env.get("erp_plugin", "generic")
            version = env.get("erp_version", "?")
            container = env.get("odoo_container", "?")
            compose_dir = env.get("compose_dir", "?")

            display_path = compose_dir.replace(home, "~") if compose_dir.startswith(home) else compose_dir
            table.add_row(name, erp, version, container, display_path)

            if last_path and compose_dir == last_path:
                highlight_row = i

        if highlight_row > 0 and table.row_count > highlight_row:
            table.move_cursor(row=highlight_row)

        self._update_status()

    def _update_status(self, msg: str | None = None) -> None:
        status = self.query_one("#status-bar", Static)
        if msg:
            status.update(f"  {msg}")
        else:
            count = len(self._envs)
            if count:
                status.update(
                    f"  {count} entorno(s)  │  Enter: seleccionar  │  a: añadir  │  x: eliminar  │  q: salir"
                )
            else:
                status.update("  a: añadir entorno  │  q: salir")

    # ── Select ────────────────────────────────────────────────────────

    def action_select(self) -> None:
        self._select_current()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._select_current()

    def _select_current(self) -> None:
        table = self.query_one("#env-table", DataTable)
        if table.row_count == 0 or not self._envs:
            return

        row_idx = table.cursor_coordinate.row
        if 0 <= row_idx < len(self._envs):
            selected = dict(self._envs[row_idx])  # copy

            self._update_status("[yellow]Conectando...[/]")
            self.refresh()

            _enrich_with_status(selected)
            set_last_env_path(selected.get("compose_dir", ""))

            self.selected_env = selected
            self.exit()

    # ── Add ───────────────────────────────────────────────────────────

    def action_add_env(self) -> None:
        form = self.query_one("#add-env-form")
        form.display = True
        self.query_one("#add-env-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-add-cancel":
            self._hide_add_form()
        elif event.button.id == "btn-add-confirm":
            self._confirm_add()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "add-env-input":
            self._confirm_add()

    def _confirm_add(self) -> None:
        path_input = self.query_one("#add-env-input", Input)
        add_status = self.query_one("#add-env-status", Static)
        raw_path = path_input.value.strip()

        if not raw_path:
            add_status.update("[red]La ruta no puede estar vacía[/]")
            return

        add_status.update("[yellow]Verificando...[/]")
        self.refresh()

        env = detect_from_path(raw_path)
        if env is None:
            add_status.update(
                f"[red]No se encontró docker-compose.yml válido en: {raw_path}[/]"
            )
            return

        add_known_env(env)
        add_status.update("")
        path_input.value = ""
        self._hide_add_form()
        self._load_table()
        self._update_status(f"[green]✔ Entorno añadido: {env.get('display_name', raw_path)}[/]")

    def _hide_add_form(self) -> None:
        self.query_one("#add-env-form").display = False
        self.query_one("#add-env-status", Static).update("")
        table = self.query_one("#env-table", DataTable)
        if table.display:
            table.focus()

    # ── Remove ────────────────────────────────────────────────────────

    def action_remove_env(self) -> None:
        table = self.query_one("#env-table", DataTable)
        if table.row_count == 0 or not self._envs:
            return

        row_idx = table.cursor_coordinate.row
        if 0 <= row_idx < len(self._envs):
            env = self._envs[row_idx]
            name = env.get("display_name", "?")
            remove_known_env(env["compose_dir"])
            self._load_table()
            self._update_status(f"[#f0b429]Eliminado: {name}[/]")
