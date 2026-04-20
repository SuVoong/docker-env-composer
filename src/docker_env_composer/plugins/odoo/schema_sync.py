"""Odoo schema sync: detect missing columns from Python field definitions and add them.

This is 100% Odoo-specific: AST parsing of fields.Char(), _inherit, ir_model,
ir_ui_view, and other Odoo internals.

Only processes modules marked as INSTALLED in ir_module_module, not all modules
in addons_path. This prevents hitting PostgreSQL's 1600 column limit.
"""

SCHEMA_SYNC_SCRIPT = r'''#!/usr/bin/env python3
"""Scan INSTALLED Odoo modules and clean orphan DB artifacts.

Phases:
- Phase 1: Add missing DB columns from Python field definitions
- Phase 2: Delete ir.ui.view owned by modules missing from disk
- Phase 3: Delete inherited views that reference non-existent fields
- Phase 4: Clean orphan ir.model + related records (fields, access, rules, data)
- Phase 5: Drop spurious columns added by previous (buggy) imports
"""

import ast
import json
import os
import re
import sys
import time
from itertools import groupby

import psycopg2

def _elapsed(start):
    secs = time.time() - start
    if secs < 60:
        return f"{secs:.1f}s"
    return f"{int(secs // 60)}m {secs % 60:.0f}s"

# Odoo field type → PostgreSQL column definition
FIELD_TYPE_MAP = {
    "Char": "VARCHAR",
    "Text": "TEXT",
    "Html": "TEXT",
    "Integer": "INTEGER",
    "Float": "DOUBLE PRECISION",
    "Monetary": "NUMERIC",
    "Boolean": "BOOLEAN DEFAULT false",
    "Date": "DATE",
    "Datetime": "TIMESTAMP WITHOUT TIME ZONE",
    "Binary": "BYTEA",
    "Selection": "VARCHAR",
    "Reference": "VARCHAR",
    "Many2one": "INTEGER",
    "Json": "JSONB",
    "Serialized": "TEXT",
    "Image": "BYTEA",
    "Properties": "JSONB",
    "PropertiesDefinition": "JSONB",
}

STORED_FIELD_TYPES = set(FIELD_TYPE_MAP.keys())

MODEL_DIRS = {"models", "wizards", "wizard"}


def get_addons_paths(conf_path):
    """Parse odoo.conf addons_path (multiline with comments)."""
    in_addons = False
    raw_lines = []
    try:
        with open(conf_path, "r") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("addons_path"):
                    in_addons = True
                    _, _, value = stripped.partition("=")
                    raw_lines.append(value)
                    continue
                if in_addons:
                    if line[0:1] in (" ", "\t", "/", "#") or stripped.startswith("/") or stripped.startswith("#"):
                        raw_lines.append(stripped)
                    else:
                        break
    except OSError:
        return []

    raw = ",".join(raw_lines)
    paths = []
    for part in raw.split(","):
        part = part.strip()
        if not part or part.startswith("#"):
            continue
        if "#" in part:
            part = part[:part.index("#")].strip()
        if part and os.path.isdir(part):
            paths.append(part)
    return paths


def get_installed_modules(cursor):
    """Get set of module technical names marked as installed in the DB."""
    cursor.execute("""
        SELECT name FROM ir_module_module
        WHERE state IN ('installed', 'to upgrade', 'to install')
    """)
    return {row[0] for row in cursor.fetchall()}


def build_module_path_map(addons_paths):
    """Map module_name → directory_path for all modules in addons_path."""
    module_map = {}
    for addons_path in addons_paths:
        if not os.path.isdir(addons_path):
            continue
        try:
            entries = os.listdir(addons_path)
        except OSError:
            continue
        for entry in entries:
            module_path = os.path.join(addons_path, entry)
            manifest = os.path.join(module_path, "__manifest__.py")
            if os.path.isdir(module_path) and os.path.isfile(manifest):
                # First match wins (same as Odoo's addons_path precedence)
                if entry not in module_map:
                    module_map[entry] = module_path
    return module_map


def find_model_py_files_for_modules(module_paths):
    """Yield .py files from models/wizards dirs of given module paths."""
    for module_path in module_paths:
        for subdir in MODEL_DIRS:
            models_dir = os.path.join(module_path, subdir)
            if not os.path.isdir(models_dir):
                continue
            for fname in os.listdir(models_dir):
                if fname.endswith(".py") and fname != "__init__.py":
                    yield os.path.join(models_dir, fname)


def _get_str_value(node):
    """Extract string value from an AST node (Str or Constant)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    # Python 3.7 compat
    if hasattr(ast, "Str") and isinstance(node, ast.Str):
        return node.s
    return None


def _is_fields_call(node):
    """Check if node is a call like fields.Char(...) and return the type name."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if (isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "fields"
            and func.attr in STORED_FIELD_TYPES):
        return func.attr
    return None


def extract_fields(filepath):
    """Extract (table_name, field_name, pg_type) using AST per-class analysis.

    Each class is parsed independently: _name/_inherit determines the model,
    and only fields assigned at that class level are yielded for that model.
    This avoids the cartesian-product bug where fields from one class would
    leak to unrelated models defined in the same file.
    """
    try:
        with open(filepath, "r", errors="ignore") as f:
            source = f.read()
    except OSError:
        return

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        # Collect _name and _inherit for THIS class only
        model_names = set()
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            for target in item.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id in ("_name", "_inherit"):
                    val = item.value
                    s = _get_str_value(val)
                    if s:
                        model_names.add(s)
                    elif isinstance(val, (ast.List, ast.Tuple)):
                        for elt in val.elts:
                            s = _get_str_value(elt)
                            if s:
                                model_names.add(s)

        if not model_names:
            continue

        # Collect field assignments for THIS class only
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            field_type = _is_fields_call(item.value)
            if not field_type:
                continue
            for target in item.targets:
                if not isinstance(target, ast.Name):
                    continue
                field_name = target.id
                if field_name.startswith("_"):
                    continue
                pg_type = FIELD_TYPE_MAP.get(field_type)
                if not pg_type:
                    continue
                for model_name in model_names:
                    table_name = model_name.replace(".", "_")
                    yield table_name, field_name, pg_type


def main():
    t_total = time.time()

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <odoo_conf_path> <database_name>")
        sys.exit(1)

    conf_path = sys.argv[1]
    db_name = sys.argv[2]

    print(f"{'=' * 60}")
    print(f"  Schema Sync — database: {db_name}")
    print(f"  Config: {conf_path}")
    print(f"{'=' * 60}")

    # ── Preparation: parse config and connect ────────────────────────
    print("\n[PREP] Parsing odoo.conf addons_path...")
    addons_paths = get_addons_paths(conf_path)
    if not addons_paths:
        print("ERROR: no addons_path found in config")
        sys.exit(1)

    print(f"  Addons paths found: {len(addons_paths)}")
    for i, p in enumerate(addons_paths[:5], 1):
        print(f"    {i}. {p}")
    if len(addons_paths) > 5:
        print(f"    ... and {len(addons_paths) - 5} more")

    print("\n[PREP] Connecting to PostgreSQL...")
    conn = psycopg2.connect(
        dbname=db_name,
        user=os.environ.get("PGUSER", "odoo"),
        password=os.environ.get("PGPASSWORD", "odoo"),
        host=os.environ.get("PGHOST", "db16"),
        port=os.environ.get("PGPORT", "5432"),
    )
    conn.autocommit = True
    cursor = conn.cursor()
    print("  Connected OK")

    # ── Discovery: installed modules vs disk ─────────────────────────
    print("\n[PREP] Querying installed modules from ir_module_module...")
    installed = get_installed_modules(cursor)
    print(f"  Installed modules in DB: {len(installed)}")

    print("[PREP] Scanning addons_path for modules on disk...")
    module_map = build_module_path_map(addons_paths)
    print(f"  Modules found on disk: {len(module_map)}")

    # ── Detect modules installed in DB but NOT on disk ───────────────
    missing_modules = installed - set(module_map.keys()) - {"base", "web"}
    if missing_modules:
        print(f"\n[PREP] Modules installed in DB but NOT on disk: {len(missing_modules)}")
        placeholders = ",".join(f"'{m}'" for m in sorted(missing_modules))
        cursor.execute(
            f"UPDATE ir_module_module SET state = 'uninstalled' "
            f"WHERE name IN ({placeholders}) "
            f"AND state IN ('installed', 'to upgrade', 'to install');"
        )
        print(f"  → Marked as uninstalled:")
        for m in sorted(missing_modules)[:20]:
            print(f"    - {m}")
        if len(missing_modules) > 20:
            print(f"    ... and {len(missing_modules) - 20} more")
    else:
        print("\n[PREP] All installed modules found on disk ✔")

    # Only process modules that are BOTH installed AND on disk
    target_paths = []
    for name in installed:
        if name in module_map:
            target_paths.append(module_map[name])

    print(f"\n[PREP] Modules to analyze (installed + on disk): {len(target_paths)}")

    # Scan ONLY installed modules for field definitions
    print("[PREP] Scanning Python files for field definitions (AST)...")
    t_scan = time.time()
    all_fields = {}
    file_count = 0
    syntax_errors = 0
    for filepath in find_model_py_files_for_modules(target_paths):
        file_count += 1
        found = False
        for table, column, pg_type in extract_fields(filepath):
            all_fields[(table, column)] = pg_type
            found = True
        if not found and filepath.endswith(".py"):
            # Count files that couldn't be parsed
            try:
                with open(filepath, "r", errors="ignore") as f:
                    ast.parse(f.read())
            except SyntaxError:
                syntax_errors += 1

    unique_tables = len({t for t, _ in all_fields.keys()})
    print(f"  Scanned {file_count} files in {_elapsed(t_scan)}")
    print(f"  Found {len(all_fields)} field definitions across {unique_tables} tables")
    if syntax_errors:
        print(f"  ⚠ {syntax_errors} files with SyntaxError (skipped)")

    # Get existing schema
    print("\n[PREP] Reading current DB schema (information_schema)...")
    cursor.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
    """)
    existing_columns = {(r[0], r[1]) for r in cursor.fetchall()}

    cursor.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    """)
    existing_tables = {r[0] for r in cursor.fetchall()}
    print(f"  DB tables: {len(existing_tables)}, total columns: {len(existing_columns)}")

    # Find missing columns (only for installed modules)
    missing = [
        (table, column, pg_type)
        for (table, column), pg_type in all_fields.items()
        if table in existing_tables and (table, column) not in existing_columns
    ]
    tables_not_in_db = {t for t, _ in all_fields.keys() if t not in existing_tables}
    if tables_not_in_db:
        print(f"  ⚠ {len(tables_not_in_db)} tables in Python code but not in DB (transient/abstract models)")

    # ── Phase 1: Add missing columns ────────────────────────────────
    print(f"\n{'─' * 60}")
    t_p1 = time.time()
    if not missing:
        print("Phase 1 (columns): no missing columns — schema is in sync ✔")
    else:
        affected_tables = len({t for t, _, _ in missing})
        print(f"Phase 1 (columns): adding {len(missing)} columns across {affected_tables} tables...")
        added = 0
        errors = 0
        error_details = []
        for table, group in groupby(sorted(missing), key=lambda x: x[0]):
            cols = list(group)
            clauses = ", ".join(
                f'ADD COLUMN IF NOT EXISTS "{col}" {pg_type}'
                for _, col, pg_type in cols
            )
            try:
                cursor.execute(f'ALTER TABLE "{table}" {clauses};')
                added += len(cols)
            except Exception as e:
                errors += len(cols)
                for _, col, pg_type in cols:
                    error_details.append(f"  ! {table}.{col}: {e}")
        # Only print errors (successes are noise at 2000+ columns)
        for line in error_details:
            print(line)
        print(f"  ✔ {added} added, {errors} errors [{_elapsed(t_p1)}]")

    # ── Phase 2: Clean orphan views from missing modules ────────────
    # Modules marked as uninstalled above may still have ir_ui_view
    # records in the DB. These orphan views cause validation errors
    # when Odoo rebuilds the combined view tree (e.g. res.config.settings).
    print(f"\n{'─' * 60}")
    t_p2 = time.time()
    if missing_modules:
        placeholders_str = ",".join(f"'{m}'" for m in sorted(missing_modules))
        cursor.execute(
            f"SELECT v.id, v.name, d.module "
            f"FROM ir_ui_view v "
            f"JOIN ir_model_data d ON d.res_id = v.id AND d.model = 'ir.ui.view' "
            f"WHERE d.module IN ({placeholders_str}) "
            f"AND NOT EXISTS ("
            f"  SELECT 1 FROM ir_ui_view c WHERE c.inherit_id = v.id"
            f")"
        )
        orphan_views = cursor.fetchall()
        if orphan_views:
            view_ids_str = ",".join(str(r[0]) for r in orphan_views)
            cursor.execute(f"DELETE FROM ir_ui_view WHERE id IN ({view_ids_str})")
            cursor.execute(
                f"DELETE FROM ir_model_data "
                f"WHERE model = 'ir.ui.view' AND res_id IN ({view_ids_str})"
            )
            print(f"Phase 2 (orphan views): cleaned {len(orphan_views)} views [{_elapsed(t_p2)}]")
            for vid, vname, vmod in orphan_views[:10]:
                print(f"  - [{vmod}] {vname}")
            if len(orphan_views) > 10:
                print(f"    ... and {len(orphan_views) - 10} more")
        else:
            print(f"Phase 2 (orphan views): no orphan views from missing modules ✔ [{_elapsed(t_p2)}]")
    else:
        print(f"Phase 2 (orphan views): no missing modules — skipped [{_elapsed(t_p2)}]")

    # ── Phase 4: Clean orphan ir.model and related records ─────────
    # When a module is removed from disk, its Python model classes
    # disappear but ir_model + ir_model_fields + ir_model_access +
    # ir_rule records remain in the DB. Odoo logs "Missing model X"
    # errors on every registry load for each orphan.
    # Strategy: find ir_model records whose model is not in the Odoo
    # registry (approximated by models that have NO installed module
    # providing them via ir_model_data).
    print(f"\n{'─' * 60}")
    t_p4 = time.time()
    print("Phase 3 (orphan models): detecting models from missing modules...")

    if missing_modules:
        placeholders_str = ",".join(f"'{m}'" for m in sorted(missing_modules))

        # Find ir.model records exclusively owned by missing modules
        # (no installed module also provides them)
        cursor.execute(f"""
            SELECT DISTINCT im.id, im.model
            FROM ir_model im
            JOIN ir_model_data d ON d.res_id = im.id
                AND d.model = 'ir.model'
                AND d.module IN ({placeholders_str})
            WHERE NOT EXISTS (
                SELECT 1 FROM ir_model_data d2
                WHERE d2.res_id = im.id
                    AND d2.model = 'ir.model'
                    AND d2.module NOT IN ({placeholders_str})
            )
        """)
        orphan_models = cursor.fetchall()

        if orphan_models:
            model_ids = [r[0] for r in orphan_models]
            ids_str = ",".join(str(i) for i in model_ids)

            # Count related records before deleting
            totals = {}

            # 4a. Delete ir_model_access (ACLs)
            cursor.execute(
                f"DELETE FROM ir_model_access WHERE model_id IN ({ids_str})"
            )
            totals["ir.model.access"] = cursor.rowcount

            # 4b. Delete ir_rule (record rules)
            cursor.execute(
                f"DELETE FROM ir_rule WHERE model_id IN ({ids_str})"
            )
            totals["ir.rule"] = cursor.rowcount

            # 4c. Delete ir_model_fields
            cursor.execute(
                f"DELETE FROM ir_model_fields WHERE model_id IN ({ids_str})"
            )
            totals["ir.model.fields"] = cursor.rowcount

            # 4d. Delete ir_model_data referencing these models and fields
            cursor.execute(
                f"DELETE FROM ir_model_data "
                f"WHERE (model = 'ir.model' AND res_id IN ({ids_str})) "
                f"OR (model = 'ir.model.fields' AND res_id NOT IN ("
                f"    SELECT id FROM ir_model_fields))"
            )
            totals["ir.model.data"] = cursor.rowcount

            # 4e. Delete ir_model_relation (many2many relation tables metadata)
            cursor.execute(
                f"DELETE FROM ir_model_relation WHERE model IN ({ids_str})"
            )
            totals["ir.model.relation"] = cursor.rowcount

            # 4f. Delete ir_model_constraint
            cursor.execute(
                f"DELETE FROM ir_model_constraint "
                f"WHERE model IN ({ids_str})"
            )
            totals["ir.model.constraint"] = cursor.rowcount

            # 4g. Finally delete ir_model itself
            cursor.execute(
                f"DELETE FROM ir_model WHERE id IN ({ids_str})"
            )
            totals["ir.model"] = cursor.rowcount

            print(f"  Cleaned {len(orphan_models)} orphan models and related records:")
            for mid, mname in orphan_models[:15]:
                print(f"    - {mname} (id={mid})")
            if len(orphan_models) > 15:
                print(f"      ... and {len(orphan_models) - 15} more")
            print(f"  Related records deleted:")
            for table, count in totals.items():
                if count > 0:
                    print(f"    {table}: {count}")
        else:
            print("  No orphan models found")
        print(f"  [{_elapsed(t_p4)}]")
    else:
        print(f"  No missing modules — skipped [{_elapsed(t_p4)}]")

    # ── Phase 3: Detect inherited views with broken field references ─
    # Covers the case where a module IS on disk but was updated and
    # lost a field/view (e.g. stock_available_immediately removed its
    # settings view but the old view persists in the DB).
    # Strategy: extract <field name="xxx"> from view arch, cross-check
    # against ir_model_fields, delete views with missing references.
    print(f"\n{'─' * 60}")
    t_p3 = time.time()
    print("Phase 4 (broken fields): scanning inherited views...")
    print("  Loading ir_model_fields from DB...")

    cursor.execute("SELECT model, name FROM ir_model_fields")
    _model_fields = {}
    for _m, _f in cursor.fetchall():
        _model_fields.setdefault(_m, set()).add(_f)
    print(f"  ir_model_fields: {sum(len(v) for v in _model_fields.values())} fields across {len(_model_fields)} models")

    print("  Loading inherited views from DB...")
    cursor.execute(
        "SELECT v.id, v.name, v.model, d.module, v.arch_db::text "
        "FROM ir_ui_view v "
        "JOIN ir_model_data d ON d.res_id = v.id AND d.model = 'ir.ui.view' "
        "WHERE v.inherit_id IS NOT NULL AND v.active = true"
    )

    _rows = cursor.fetchall()
    print(f"  Inherited views to check: {len(_rows)}")
    print("  Analyzing view arch for broken field references...")
    _fre = re.compile(r'<field[^>]*\sname="(\w+)"')
    _broken = []
    for _vid, _vn, _vm, _vmod, _arch in _rows:
        if not _arch or not _vm:
            continue
        # arch_db is JSONB — parse to get the XML string
        try:
            _d = json.loads(_arch)
            _xml = next(iter(_d.values())) if isinstance(_d, dict) else _arch
        except (ValueError, StopIteration):
            # Fallback: unescape JSONB text representation
            _xml = _arch.replace('\\"', '"')
        _refs = set(_fre.findall(_xml))
        if not _refs:
            continue
        _exist = _model_fields.get(_vm)
        if not _exist:
            continue
        _miss = _refs - _exist
        if _miss:
            _broken.append((_vid, _vn, _vm, _vmod, _miss))

    if _broken:
        # Only delete leaf views (no children inheriting from them)
        _bids = [str(v[0]) for v in _broken]
        cursor.execute(
            "SELECT DISTINCT inherit_id FROM ir_ui_view "
            "WHERE inherit_id IN (" + ",".join(_bids) + ")"
        )
        _pids = {str(r[0]) for r in cursor.fetchall()}
        _safe = [v for v in _broken if str(v[0]) not in _pids]
        _skip = [v for v in _broken if str(v[0]) in _pids]

        if _safe:
            _del = ",".join(str(v[0]) for v in _safe)
            cursor.execute(f"DELETE FROM ir_ui_view WHERE id IN ({_del})")
            cursor.execute(
                f"DELETE FROM ir_model_data "
                f"WHERE model = 'ir.ui.view' AND res_id IN ({_del})"
            )
            # Group by module for compact output
            _by_mod = {}
            for _vid, _vn, _vm, _vmod, _miss in _safe:
                _by_mod.setdefault(_vmod, []).append(_vn)
            print(f"  Cleaned {len(_safe)} views with broken field references:")
            for _mod in sorted(_by_mod):
                _views = _by_mod[_mod]
                if len(_views) <= 2:
                    for _v in _views:
                        print(f"    - [{_mod}] {_v}")
                else:
                    print(f"    - [{_mod}] {len(_views)} views")

        if _skip:
            print(f"  ⚠ {len(_skip)} broken views have children (not deleted):")
            for _vid, _vn, _vm, _vmod, _miss in _skip[:5]:
                print(f"    ! [{_vmod}] {_vn} ({_vm})")
            if len(_skip) > 5:
                print(f"    ... and {len(_skip) - 5} more")

        if not _safe and not _skip:
            print("  No broken views found")
        print(f"  [{_elapsed(t_p3)}]")
    else:
        print(f"  No broken field references found ✔ [{_elapsed(t_p3)}]")

    # ── Phase 5: Detect and drop spurious columns ──────────────────
    # Previous versions of the schema sync had a cartesian-product bug:
    # all fields found in a .py file were assigned to ALL models in that
    # file. This created columns on wrong tables (e.g. product_tmpl_id
    # on product_template, which caused "ambiguous column" SQL errors).
    # This phase detects columns that exist in the DB but do NOT belong
    # to the table according to the (now-correct) AST field map.
    print(f"\n{'─' * 60}")
    t_p5 = time.time()
    print("Phase 5 (spurious columns): checking for wrongly-added columns...")

    # Build a set of known Odoo core columns that should NEVER be dropped
    # (these exist on most tables by default)
    CORE_COLUMNS = {
        "id", "create_uid", "create_date", "write_uid", "write_date",
        "__last_update",
    }

    # Cross-reference: for each (table, column) that Phase 1 would have
    # added, check if it's NOT in the correct field map (all_fields).
    # We only check tables that have at least one field in all_fields
    # (to avoid touching tables we don't know about).
    known_tables = {t for t, _ in all_fields.keys()}
    print(f"  Tables to check: {len(known_tables)}")

    # Get all columns that were likely added by schema sync (not in the
    # original dump). We detect them by checking: column exists in DB,
    # table is known (has fields in our map), but (table, column) is NOT
    # in all_fields AND column is not a core column.
    # To be safe, we also check that ir_model_fields does NOT list the
    # column for the model — this avoids dropping columns defined via
    # _fields rather than class-level assignment (e.g. computed fields).
    # Bulk query 1: todas las columnas de las tablas conocidas de una vez
    cursor.execute(
        "SELECT table_name, column_name "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ANY(%s)",
        (list(known_tables),)
    )
    db_cols_map = {}
    for tname, cname in cursor.fetchall():
        db_cols_map.setdefault(tname, set()).add(cname)

    # Bulk query 2: todos los campos de ir_model_fields para los modelos conocidos
    known_models = [t.replace("_", ".") for t in known_tables]
    cursor.execute(
        "SELECT model, name FROM ir_model_fields WHERE model = ANY(%s)",
        (known_models,)
    )
    ir_fields_map = {}
    for mname, fname in cursor.fetchall():
        ir_fields_map.setdefault(mname, set()).add(fname)

    spurious = []
    for table in known_tables:
        model_name = table.replace("_", ".")
        db_cols = db_cols_map.get(table, set())
        ir_fields = ir_fields_map.get(model_name, set())

        # Expected columns from Python code
        py_fields = {col for (t, col) in all_fields.keys() if t == table}

        for col in db_cols:
            if col in CORE_COLUMNS:
                continue
            if col in py_fields:
                continue
            if col in ir_fields:
                continue
            # This column is not in Python code NOR ir_model_fields.
            # Only flag it if it looks like it was added by schema sync
            # (not a FK or standard column). Check if ANY other table
            # has this column in its Python fields — that's the
            # cartesian-product signature.
            other_tables = [
                t for (t, c) in all_fields.keys()
                if c == col and t != table
            ]
            if other_tables:
                spurious.append((table, col, other_tables))

    if spurious:
        dropped = 0
        errors = 0
        print(f"  Found {len(spurious)} spurious columns (cartesian-product artifacts):")
        for table, col, sources in sorted(spurious):
            try:
                cursor.execute(
                    f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS "{col}";'
                )
                dropped += 1
                print(f"    - DROPPED {table}.{col} (belongs to {sources[0]}, not {table})")
            except Exception as e:
                errors += 1
                print(f"    ! {table}.{col}: cannot drop — {e}")
        print(f"  Result: {dropped} dropped, {errors} errors [{_elapsed(t_p5)}]")
    else:
        print(f"  No spurious columns found ✔ [{_elapsed(t_p5)}]")

    # ── Final summary ────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  Schema Sync complete — total time: {_elapsed(t_total)}")
    print(f"{'=' * 60}")
    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
'''
