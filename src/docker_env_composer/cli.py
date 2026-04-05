"""CLI entry point: `dec` command."""

from __future__ import annotations

import sys
import click

from .core.detect import detect_environment


def _get_env(ctx: click.Context) -> dict:
    """Get the detected environment from context, or fail."""
    env = ctx.obj
    if not env:
        click.echo("Error: no se detectó docker-compose.yml en el directorio actual ni padres.")
        click.echo("Ejecuta dec desde el directorio de tu Docker Odoo.")
        sys.exit(1)
    return env


@click.group()
@click.pass_context
def main(ctx: click.Context) -> None:
    """dec — Docker Env Composer: gestiona entornos sobre Docker."""
    ctx.ensure_object(dict)
    env = detect_environment()
    ctx.obj = env


# --- create ---


@main.command()
@click.option("--name", "-n", help="Nombre de la base de datos")
@click.option("--description", "-d", help="Descripción del entorno")
@click.option("--template", "-t", help="Template a usar (nombre sin extensión)")
@click.option("--ttl", type=int, help="Días hasta expiración")
@click.option("--modules", "-m", help="Módulos extra a instalar (comma-separated)")
@click.option("--no-tui", is_flag=True, help="Modo no interactivo (requiere --name)")
@click.pass_context
def create(
    ctx: click.Context,
    name: str | None,
    description: str | None,
    template: str | None,
    ttl: int | None,
    modules: str | None,
    no_tui: bool,
) -> None:
    """Crear un nuevo entorno/base de datos."""
    env = _get_env(ctx)

    if no_tui:
        if not name:
            click.echo("Error: --name es obligatorio en modo --no-tui")
            sys.exit(1)
        _create_direct(env, name, description or "", template, ttl, modules)
        return

    # TUI mode
    from .tui.app import run_create_wizard

    run_create_wizard(env, name, description, template, ttl, modules)


def _create_direct(
    env: dict,
    name: str,
    description: str,
    template: str | None,
    ttl: int | None,
    modules: str | None,
) -> None:
    """Non-interactive database creation."""
    from .core.postgres import PgConfig, create_database, database_exists
    from .core.registry import register_database
    from .core.sanitize import validate_db_name, validate_module_list
    from .core.templates import create_from_template

    try:
        validate_db_name(name)
    except ValueError as e:
        click.echo(f"Error: {e}")
        sys.exit(1)

    module_list = [m.strip() for m in modules.split(",")] if modules else []
    if module_list:
        try:
            validate_module_list(module_list)
        except ValueError as e:
            click.echo(f"Error en módulo: {e}")
            sys.exit(1)

    pg = PgConfig.from_env(env)

    if database_exists(pg, name):
        click.echo(f"Error: la base de datos '{name}' ya existe.")
        sys.exit(1)

    from .core.detect import get_plugin_for_env
    plugin = get_plugin_for_env(env)
    container = env.get("odoo_container", "")

    if template:
        click.echo(f"Creando '{name}' desde template '{template}'...")
        create_from_template(pg, name, template, container, plugin, module_list or None, callback=click.echo)
    else:
        click.echo(f"Creando '{name}'...")
        create_database(pg, name)
        if module_list:
            click.echo(f"Instalando módulos: {', '.join(module_list)}")
            plugin.install_modules(container, name, module_list)

    register_database(
        db_name=name,
        description=description,
        erp_version=env.get("erp_version", "?"),
        template=template,
        ttl_days=ttl,
        modules=module_list,
    )
    click.echo(f"Base de datos '{name}' creada.")


# --- list ---


@main.command("list")
@click.pass_context
def list_cmd(ctx: click.Context) -> None:
    """Listar entornos/bases de datos."""
    env = _get_env(ctx)

    from .tui.app import run_dashboard

    run_dashboard(env)


# --- clean ---


@main.command()
@click.option("--yes", "-y", is_flag=True, help="Saltar confirmación")
@click.pass_context
def clean(ctx: click.Context, yes: bool) -> None:
    """Eliminar bases de datos expiradas."""
    env = _get_env(ctx)

    from .core.detect import get_plugin_for_env
    from .core.postgres import PgConfig, database_exists, drop_database
    from .core.registry import get_expired, unregister_database

    pg = PgConfig.from_env(env)
    plugin = get_plugin_for_env(env)
    container = env.get("odoo_container", "")
    expired = get_expired()

    if not expired:
        click.echo("No hay bases de datos expiradas.")
        return

    click.echo(f"Bases de datos expiradas ({len(expired)}):")
    for db in expired:
        click.echo(f"  - {db['name']}: {db['description']} (expiró: {db['expires_at'][:10]})")

    if not yes:
        click.confirm("¿Eliminar todas?", abort=True)

    for db in expired:
        name = db["name"]
        if database_exists(pg, name):
            drop_database(pg, name)
            plugin.remove_filestore(container, name)
        unregister_database(name)
        click.echo(f"  Eliminada: {name}")

    click.echo(f"{len(expired)} base(s) de datos eliminadas.")


# --- template ---


@main.group()
def template() -> None:
    """Gestionar templates (importar ZIP → PG template)."""
    pass


@template.command("import")
@click.argument("zip_name")
@click.pass_context
def template_import(ctx: click.Context, zip_name: str) -> None:
    """Importar un ZIP como PostgreSQL template para copias instantáneas."""
    env = _get_env(ctx)

    from .core.postgres import PgConfig
    from .core.sanitize import validate_db_name
    from .core.templates import import_zip_as_pg_template, list_zip_templates

    # Find the ZIP by name (with or without .zip)
    zip_name_clean = zip_name.replace(".zip", "")

    try:
        validate_db_name(zip_name_clean)
    except ValueError as e:
        click.echo(f"Error: nombre de template invalido — {e}")
        sys.exit(1)

    pg = PgConfig.from_env(env)
    templates = list_zip_templates(env["templates_dir"])
    match = next((t for t in templates if t["name"] == zip_name_clean), None)

    if not match:
        click.echo(f"Error: template '{zip_name}' no encontrado en {env['templates_dir']}")
        click.echo("Templates disponibles:")
        for t in templates:
            click.echo(f"  - {t['name']} ({t['size_mb']} MB)")
        sys.exit(1)

    click.echo(f"Importando '{match['name']}' ({match['size_mb']} MB) como PG template...")
    click.echo("(este proceso puede tardar varios minutos)\n")

    from .core.detect import get_plugin_for_env
    plugin = get_plugin_for_env(env)

    pg_name, summary = import_zip_as_pg_template(
        pg, match["path"], zip_name_clean, env.get("odoo_container", ""),
        plugin=plugin,
        callback=lambda msg: click.echo(f"  {msg}"),
    )

    # Report sanitization results
    if summary.get("restore_errors", 0) > 0:
        click.echo(f"\n  Errores en restore: {summary['restore_errors']} (ignorados)")
    if summary.get("fixes_applied", 0) > 0:
        click.echo(f"  Limpieza aplicada ({summary['fixes_applied']} registros):")
        for detail in summary.get("details", []):
            click.echo(f"    - {detail}")
    else:
        click.echo("\n  Base de datos limpia, sin correcciones necesarias.")

    click.echo(f"\nTemplate PostgreSQL '{pg_name}' creado. Las copias serán instantáneas.")


@template.command("list")
@click.pass_context
def template_list(ctx: click.Context) -> None:
    """Listar templates disponibles (ZIP y PG)."""
    env = _get_env(ctx)

    from .core.postgres import PgConfig
    from .core.templates import list_pg_templates, list_zip_templates

    pg = PgConfig.from_env(env)

    zips = list_zip_templates(env["templates_dir"])
    pg_tmpls = list_pg_templates(pg)

    if zips:
        click.echo("Templates ZIP (requieren importar):")
        for t in zips:
            imported = f"dec_tmpl_{t['name']}" in pg_tmpls
            status = " [importado]" if imported else ""
            click.echo(f"  - {t['name']} ({t['size_mb']} MB){status}")

    if pg_tmpls:
        click.echo("\nTemplates PostgreSQL (copias instantáneas):")
        for name in pg_tmpls:
            click.echo(f"  - {name}")

    if not zips and not pg_tmpls:
        click.echo("No hay templates disponibles.")
        click.echo(f"Coloca archivos .zip en: {env['templates_dir']}")
