"""Create wizard: interactive form to create a new database."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static

from ...core.detect import get_plugin_for_env
from ...core.postgres import PgConfig, create_database, database_exists
from ...core.registry import register_database
from ...core.sanitize import validate_db_name, validate_module_list
from ...core.templates import create_from_template, list_pg_templates, list_zip_templates

CSS_PATH = Path(__file__).parent.parent / "styles.tcss"

TTL_OPTIONS = [
    ("Sin expiración", None),
    ("3 días", 3),
    ("7 días", 7),
    ("30 días", 30),
]


class CreateWizardApp(App):
    """Wizard form to create a new Odoo database."""

    CSS_PATH = CSS_PATH
    TITLE = "dec — Crear entorno"
    BINDINGS = [
        Binding("escape", "quit", "Cancelar"),
    ]

    def __init__(
        self,
        env: dict,
        name: str | None = None,
        description: str | None = None,
        template: str | None = None,
        ttl: int | None = None,
        modules: str | None = None,
    ) -> None:
        super().__init__()
        self.env = env
        self.initial_name = name or ""
        self.initial_desc = description or ""
        self.initial_template = template
        self.initial_ttl = ttl
        self.initial_modules = modules or ""

    def compose(self) -> ComposeResult:
        yield Header()

        version = self.env.get("erp_version", "?")
        container = self.env.get("odoo_container", "?")
        odoo_ok = "✔" if self.env.get("odoo_running") else "✘"
        pg_ok = "✔" if self.env.get("pg_running") else "✘"

        with Vertical(id="create-container"):
            with Vertical(id="create-form"):
                yield Static(f"▶ Crear entorno — Odoo {version}", id="form-title")
                yield Static(
                    f"Container: {container} {odoo_ok}  │  PG: {self.env.get('pg_port', '?')} {pg_ok}"
                )

                yield Label("Nombre de la base de datos:")
                yield Input(
                    value=self.initial_name,
                    placeholder="ej: TPV_test",
                    id="db-name",
                )

                yield Label("Descripción:")
                yield Input(
                    value=self.initial_desc,
                    placeholder="ej: Test POS",
                    id="db-desc",
                )

                yield Label("Template:")
                template_opts = self._template_options()
                default_val = self._default_template(template_opts)
                yield Select(
                    template_opts,
                    value=default_val,
                    allow_blank=False,
                    id="db-template",
                )

                yield Label("Módulos extra (comma-separated):")
                yield Input(
                    value=self.initial_modules,
                    placeholder="ej: point_of_sale",
                    id="db-modules",
                )

                yield Label("Expiración:")
                yield Select(
                    [(label, val) for label, val in TTL_OPTIONS],
                    value=self.initial_ttl,
                    id="db-ttl",
                )

                yield Static("")
                yield Static(id="status-msg")

                with Horizontal(id="buttons"):
                    yield Button("✔ Crear", variant="primary", id="btn-create")
                    yield Button("✘ Cancelar", variant="default", id="btn-cancel")

        yield Footer()

    def _template_options(self) -> list[tuple[str, str]]:
        """Build template selection options from PG templates and ZIPs."""
        options: list[tuple[str, str]] = [("Sin template (solo base)", "__none__")]

        pg = PgConfig.from_env(self.env)
        pg_tmpls = list_pg_templates(pg)
        for name in pg_tmpls:
            clean = name.replace("dec_tmpl_", "")
            options.append((f"{clean} (instantáneo)", clean))

        zips = list_zip_templates(self.env.get("templates_dir", ""))
        pg_names = {t.replace("dec_tmpl_", "") for t in pg_tmpls}
        for z in zips:
            if z["name"] not in pg_names:
                options.append((f"{z['name']} (ZIP — requiere importar)", f"zip:{z['name']}"))

        return options

    def _default_template(self, options: list[tuple[str, str]]) -> str:
        """Pick the default template: initial_template > first PG template > __none__."""
        if self.initial_template:
            return self.initial_template
        # First PG template (skip __none__ at index 0)
        for _label, val in options[1:]:
            if not val.startswith("zip:"):
                return val
        return "__none__"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.exit()
            return

        if event.button.id == "btn-create":
            self._do_create()

    def _do_create(self) -> None:
        status = self.query_one("#status-msg", Static)
        name_input = self.query_one("#db-name", Input)
        desc_input = self.query_one("#db-desc", Input)
        template_select = self.query_one("#db-template", Select)
        modules_input = self.query_one("#db-modules", Input)
        ttl_select = self.query_one("#db-ttl", Select)

        db_name = name_input.value.strip()
        description = desc_input.value.strip()
        template_val = template_select.value
        modules_str = modules_input.value.strip()
        ttl_val = ttl_select.value

        if not db_name:
            status.update("[red]Error: el nombre es obligatorio[/]")
            return

        try:
            validate_db_name(db_name)
        except ValueError as e:
            status.update(f"[red]Error: {e}[/]")
            return

        module_list = [m.strip() for m in modules_str.split(",") if m.strip()] if modules_str else []
        if module_list:
            try:
                validate_module_list(module_list)
            except ValueError as e:
                status.update(f"[red]Error en módulo: {e}[/]")
                return

        pg = PgConfig.from_env(self.env)

        if database_exists(pg, db_name):
            status.update(f"[red]Error: '{db_name}' ya existe[/]")
            return

        if template_val and str(template_val).startswith("zip:"):
            status.update("[red]Error: template ZIP no importado. Ejecuta: dec template import <nombre>[/]")
            return

        template_name = None if template_val in (None, "__none__", Select.BLANK) else str(template_val)
        ttl_days = int(ttl_val) if ttl_val and ttl_val != Select.BLANK else None

        status.update(f"[yellow]Creando '{db_name}'...[/]")
        self.refresh()

        try:
            plugin = get_plugin_for_env(self.env)
            container = self.env["odoo_container"]

            timing: dict = {}
            if template_name:
                def _tui_log(msg: str):
                    status.update(f"[yellow]{msg.strip()}[/]")
                    self.refresh()
 
                timing = create_from_template(
                    pg, db_name, template_name, container,
                    plugin=plugin,
                    extra_modules=module_list or None,
                    callback=_tui_log,
                )
            else:
                create_database(pg, db_name)
                if module_list:
                    status.update(f"[yellow]Instalando módulos: {', '.join(module_list)}...[/]")
                    self.refresh()
                    plugin.install_modules(container, db_name, module_list)

            register_database(
                db_name=db_name,
                description=description,
                erp_version=self.env.get("erp_version", "?"),
                template=template_name,
                ttl_days=ttl_days,
                modules=module_list,
            )

            total = timing.get("total")
            time_info = f" ({total}s)" if total is not None else ""
            status.update(f"[green]Base de datos '{db_name}' creada correctamente{time_info}.[/]")

        except Exception as e:
            status.update(f"[red]Error: {e}[/]")
