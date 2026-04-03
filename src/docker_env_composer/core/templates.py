"""Template management: ZIP files and PostgreSQL template databases.
 
This module is ERP-agnostic. ERP-specific steps (schema sync, module install,
filestore, neutralization) are delegated to an ERPPlugin instance.
"""

from __future__ import annotations

import tempfile
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..plugins.base import ERPPlugin


def _elapsed(start: float) -> str:
    """Return formatted elapsed time since start."""
    secs = time.time() - start
    if secs < 60:
        return f"{secs:.1f}s"
    return f"{int(secs // 60)}m {secs % 60:.0f}s"

from .postgres import (
    PgConfig,
    create_database,
    database_exists,
    register_as_pg_template,
    unregister_pg_template,
)

def list_zip_templates(templates_dir: str) -> list[dict]:
    """List available ZIP templates in the templates directory."""
    tdir = Path(templates_dir)
    if not tdir.is_dir():
        return []
    templates = []
    for f in sorted(tdir.glob("*.zip")):
        templates.append({
            "name": f.stem,
            "path": str(f),
            "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
        })
    return templates

def list_pg_templates(pg: PgConfig) -> list[str]:
    """List PostgreSQL template databases (dec-managed)."""
    result = pg.run_psql(
        "postgres",
        "SELECT datname FROM pg_database "
        "WHERE datistemplate = true AND datname LIKE 'dec_tmpl_%' "
        "ORDER BY datname;",
    )
    if result.returncode != 0:
        return []
    lines = result.stdout.strip().split("\n")
    return [line.strip() for line in lines[2:] if line.strip() and not line.strip().startswith("(")]

def import_zip_as_pg_template(
    pg: PgConfig,
    zip_path: str,
    template_name: str,
    container: str,
    plugin: ERPPlugin | None = None,
    callback=None,
) -> tuple[str, dict]:
    """Import a ZIP dump into a PostgreSQL template database for instant copies.

    The ZIP should contain:
      - dump.sql: PostgreSQL plain-text dump
      - filestore/ (optional): ERP filestore directory

    ERP-specific steps (schema sync, neutralization) are delegated to the plugin.
    If no plugin is provided, only dump restore + PG template are done.

    Returns (pg_template_name, sanitize_summary).
    """
    pg_template_name = f"dec_tmpl_{template_name}"
    t_pipeline = time.time()

    def _log(msg: str):
        if callback:
            callback(msg)

    _log(f"{'=' * 55}")
    _log(f"  Pipeline import: {template_name} → {pg_template_name}")
    _log(f"  ZIP: {zip_path}")
    _log(f"  Container: {container}")
    _log(f"{'=' * 55}")

    # Drop if already exists
    if database_exists(pg, pg_template_name):
        _log(f"\n⟳ Eliminando template anterior '{pg_template_name}'...")
        unregister_pg_template(pg, pg_template_name)
        from .postgres import drop_database
        drop_database(pg, pg_template_name)
        _log("  Eliminado")

    with tempfile.TemporaryDirectory() as tmp_dir:
        _log("\n⟳ Extrayendo ZIP...")
        t_extract = time.time()
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            _log(f"  Ficheros en ZIP: {len(members)}")
            # Validate members against Zip Slip (path traversal)
            tmp_resolved = Path(tmp_dir).resolve()
            for member in members:
                member_path = (tmp_resolved / member).resolve()
                if not str(member_path).startswith(str(tmp_resolved)):
                    raise ValueError(
                        f"ZIP contains path traversal entry: {member}"
                    )
            zf.extractall(tmp_dir)
        _log(f"  Extraído en {_elapsed(t_extract)}")

        tmp_path = Path(tmp_dir)
        dump_file = tmp_path / "dump.sql"
        if not dump_file.is_file():
            raise FileNotFoundError(f"dump.sql not found in {zip_path}")

        dump_size_mb = round(dump_file.stat().st_size / (1024 * 1024), 1)
        _log(f"  dump.sql: {dump_size_mb} MB")

        filestore_dir = tmp_path / "filestore"
        if filestore_dir.is_dir():
            fs_count = sum(1 for _ in filestore_dir.rglob("*") if _.is_file())
            _log(f"  filestore: {fs_count} ficheros")
        else:
            _log("  filestore: no incluido en el ZIP")

        sanitize_summary = {"errors_found": 0, "fixes_applied": 0, "details": []}

        # ── Step 1/5: Restore dump.sql ───────────────────────────────
        # Loads the SQL dump into a fresh database. Errors at this stage
        # are expected (missing extensions, role mismatches) and will be
        # resolved in subsequent steps.
        _log(f"\n{'─' * 55}")
        _log(f"1/5 Restaurando dump.sql ({dump_size_mb} MB) en base de datos nueva...")
        t_step1 = time.time()
        _log(f"   Creando base de datos '{pg_template_name}'...")
        create_database(pg, pg_template_name)
        _log(f"   Ejecutando psql -f dump.sql (esto puede tardar)...")
        result = pg.run_psql_file(pg_template_name, str(dump_file), on_error_stop=False)

        error_count = 0
        if result.stderr:
            error_lines = [l for l in result.stderr.split("\n") if "ERROR:" in l]
            error_count = len(error_lines)
            if error_count > 0:
                _log(f"   ⚠ {error_count} errores en restore (se resolverán en pasos 2-4)")
                # Show first few errors for visibility
                for err in error_lines[:5]:
                    _log(f"     {err.strip()[:120]}")
                if error_count > 5:
                    _log(f"     ... y {error_count - 5} errores más")
        _log(f"   Paso 1 completado [{_elapsed(t_step1)}]")

        # ── Step 2: ERP post-restore (plugin) ───────────────────────
        # Delegates to plugin: schema rebuild, schema sync, etc.
        if plugin:
            _log(f"\n{'─' * 55}")
            _log(f"2/5 Post-restore ERP ({plugin.name})...")
            t_step2 = time.time()
            post_result = plugin.post_restore(
                container, pg_template_name, pg, callback=_log,
            )
            sanitize_summary["fixes_applied"] += post_result.get("fixes_applied", 0)
            sanitize_summary["details"].extend(post_result.get("details", []))
            _log(f"   ✔ Post-restore completado [{_elapsed(t_step2)}]")
        else:
            _log(f"\n{'─' * 55}")
            _log("2/5 Sin plugin ERP — saltando post-restore")

        # ── Step 3: Restore filestore (plugin) ─────────────────────
        _log(f"\n{'─' * 55}")
        filestore_dir = tmp_path / "filestore"
        if filestore_dir.is_dir() and plugin:
            _log("3/5 Restaurando filestore en contenedor...")
            t_step3 = time.time()
            _log("   Copiando filestore al contenedor...")
            plugin.restore_filestore(container, pg_template_name, str(filestore_dir))
            _log(f"   ✔ Filestore restaurado [{_elapsed(t_step3)}]")
        elif filestore_dir.is_dir():
            _log("3/5 Filestore encontrado pero sin plugin ERP — saltando")
        else:
            _log("3/5 Sin filestore en el ZIP — saltando")

        # ── Step 4: Neutralize runtime artifacts (plugin) ──────────
        if plugin:
            _log(f"\n{'─' * 55}")
            _log(f"4/5 Neutralizando artefactos de runtime ({plugin.name})...")
            t_step4 = time.time()
            neut_result = plugin.neutralize(pg, pg_template_name, callback=_log)
            sanitize_summary["fixes_applied"] += neut_result.get("fixes_applied", 0)
            sanitize_summary["details"].extend(neut_result.get("details", []))
            _log(f"   ✔ Neutralización completada [{_elapsed(t_step4)}]")
        else:
            _log(f"\n{'─' * 55}")
            _log("4/5 Sin plugin ERP — saltando neutralización")

        # ── Step 5: Register as PG template ────────────────────────
        # Marks the database as a PostgreSQL template. Future copies
        # via CREATE DATABASE ... TEMPLATE are near-instant (filesystem
        # copy, no dump/restore).
        _log(f"\n{'─' * 55}")
        _log("5/5 Marcando como template PostgreSQL...")
        register_as_pg_template(pg, pg_template_name)
        _log("   ✔ Template registrado (copias instantáneas habilitadas)")

        # ── Final ────────────────────────────────────────────────────
        _log(f"\n{'=' * 55}")
        _log(f"  ✔ Pipeline completado — tiempo total: {_elapsed(t_pipeline)}")
        _log(f"{'=' * 55}")

    sanitize_summary["restore_errors"] = error_count
    return pg_template_name, sanitize_summary


def create_from_template(
    pg: PgConfig,
    db_name: str,
    template_name: str,
    container: str,
    plugin: ERPPlugin | None = None,
    extra_modules: list[str] | None = None,
    callback=None,
) -> dict:
    """Create a database from a PG template (instant) + optionally install extra modules.
    Returns a timing dict with elapsed seconds for each step and total.
    """
    pg_template_name = f"dec_tmpl_{template_name}"

    def _log(msg: str):
        if callback:
            callback(msg)

    if not database_exists(pg, pg_template_name):
        raise RuntimeError(
            f"PG template '{pg_template_name}' not found. "
            f"Import it first with: dec template import {template_name}"
        )

    t_total = time.time()
    timing: dict[str, float] = {}

    from .postgres import create_from_pg_template
 
    if plugin:
        # Steps 1+2 are independent: run DB copy and filestore copy in parallel.
        from concurrent.futures import ThreadPoolExecutor
 
        _log(f"  Creando base de datos y copiando filestore en paralelo...")
        t_parallel = time.time()
 
        db_exc: list[BaseException] = []
        fs_exc: list[BaseException] = []
 
        def _copy_db():
            try:
                create_from_pg_template(pg, db_name, pg_template_name)
                timing["db_copy"] = round(time.time() - t_parallel, 2)
            except Exception as e:
                db_exc.append(e)
 
        def _copy_fs():
            try:
                plugin.copy_filestore(container, pg_template_name, db_name)
                timing["filestore_copy"] = round(time.time() - t_parallel, 2)
            except Exception as e:
                fs_exc.append(e)
 
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_db = executor.submit(_copy_db)
            f_fs = executor.submit(_copy_fs)
            f_db.result()
            f_fs.result()
 
        if db_exc:
            raise db_exc[0]
        if fs_exc:
            raise fs_exc[0]
 
        _log(f"  ✔ Base de datos y filestore listos [{_elapsed(t_parallel)}]")
    else:
        # No plugin: only DB copy needed.
        _log(f"  Creando base de datos '{db_name}' desde template '{pg_template_name}'...")
        t_db = time.time()
        create_from_pg_template(pg, db_name, pg_template_name)
        timing["db_copy"] = round(time.time() - t_db, 2)
        _log(f"  ✔ Base de datos creada [{_elapsed(t_db)}]")

    # Step 3: Install extra modules (optional)
    if extra_modules and plugin:
        _log(f"  Instalando módulos extra: {', '.join(extra_modules)}...")
        t_mod = time.time()
        plugin.install_modules(container, db_name, extra_modules)
        timing["modules_install"] = round(time.time() - t_mod, 2)
        _log(f"  ✔ Módulos instalados [{_elapsed(t_mod)}]")

    timing["total"] = round(time.time() - t_total, 2)
    _log(f"  ✔ Entorno '{db_name}' listo — tiempo total: {_elapsed(t_total)}")

    return timing