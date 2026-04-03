"""PostgreSQL operations via psql/createdb/dropdb on the Docker-exposed port."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime

from .sanitize import sanitize_sql_literal, validate_db_name


@dataclass
class PgConfig:
    host: str = "localhost"
    port: int = 5432
    user: str = "odoo"
    password: str = "odoo"

    @classmethod
    def from_env(cls, env: dict) -> PgConfig:
        return cls(
            host="localhost",
            port=env.get("pg_port", 5432),
            user=env.get("pg_user", "odoo"),
            password=env.get("pg_password", "odoo"),
        )

    def _env(self) -> dict:
        e = os.environ.copy()
        e["PGPASSWORD"] = self.password
        return e

    def _base_args(self) -> list[str]:
        return ["-h", self.host, "-p", str(self.port), "-U", self.user]

    def run_psql(self, database: str, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["psql", *self._base_args(), "-d", database, "-c", command],
            capture_output=True,
            text=True,
            env=self._env(),
            timeout=30,
        )

    def run_psql_file(
        self, database: str, filepath: str, on_error_stop: bool = False,
    ) -> subprocess.CompletedProcess:
        cmd = [
            "psql", *self._base_args(), "-d", database,
            "-f", filepath, "--quiet", "--no-psqlrc",
        ]
        if on_error_stop:
            cmd.append("--set=ON_ERROR_STOP=on")
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=self._env(),
            timeout=600,
        )


def list_databases(pg: PgConfig) -> list[str]:
    """List all non-system databases."""
    result = pg.run_psql(
        "postgres",
        "SELECT datname FROM pg_database "
        "WHERE datistemplate = false AND datname NOT IN ('postgres') "
        "ORDER BY datname;",
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to list databases: {result.stderr}")
    lines = result.stdout.strip().split("\n")
    # Skip header (name + separator lines) and footer
    databases = []
    for line in lines[2:]:
        name = line.strip()
        if name and not name.startswith("(") and name != "":
            databases.append(name)
    return databases


def database_exists(pg: PgConfig, db_name: str) -> bool:
    """Check if a database exists."""
    safe_name = sanitize_sql_literal(db_name)
    result = pg.run_psql(
        "postgres",
        f"SELECT 1 FROM pg_database WHERE datname = {safe_name};",
    )
    return "1" in result.stdout


def create_database(pg: PgConfig, db_name: str, template: str = "template0") -> None:
    """Create a new database."""
    validate_db_name(db_name)
    if template != "template0":
        validate_db_name(template)
    env = pg._env()
    args = ["createdb", *pg._base_args()]
    if template != "template0":
        args.extend(["-T", template])
    else:
        args.extend([
            "-T", "template0",
            "--encoding=UTF8",
            "--lc-collate=C",
            "--lc-ctype=en_US.utf8",
        ])
    args.extend(["--", db_name])

    result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create database '{db_name}': {result.stderr}")


def create_from_pg_template(pg: PgConfig, db_name: str, template_db: str) -> None:
    """Create database from a PostgreSQL template database (instant copy)."""
    safe_template = sanitize_sql_literal(template_db)
    # Disconnect all sessions from template first
    pg.run_psql(
        "postgres",
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = {safe_template} AND pid <> pg_backend_pid();",
    )
    create_database(pg, db_name, template=template_db)


def drop_database(pg: PgConfig, db_name: str) -> None:
    """Drop a database."""
    validate_db_name(db_name)
    safe_name = sanitize_sql_literal(db_name)
    # Terminate active connections
    pg.run_psql(
        "postgres",
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = {safe_name} AND pid <> pg_backend_pid();",
    )
    env = pg._env()
    result = subprocess.run(
        ["dropdb", *pg._base_args(), "--if-exists", "--", db_name],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to drop database '{db_name}': {result.stderr}")


def database_size(pg: PgConfig, db_name: str) -> str:
    """Get human-readable database size."""
    safe_name = sanitize_sql_literal(db_name)
    result = pg.run_psql(
        "postgres",
        f"SELECT pg_size_pretty(pg_database_size({safe_name}));",
    )
    if result.returncode == 0:
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 3:
            return lines[2].strip()
    return "?"


def register_as_pg_template(pg: PgConfig, db_name: str) -> None:
    """Mark a database as a PostgreSQL template (for instant copies)."""
    safe_name = sanitize_sql_literal(db_name)
    pg.run_psql(
        "postgres",
        f"UPDATE pg_database SET datistemplate = true WHERE datname = {safe_name};",
    )


def unregister_pg_template(pg: PgConfig, db_name: str) -> None:
    """Unmark a database as a PostgreSQL template (required before dropping)."""
    safe_name = sanitize_sql_literal(db_name)
    pg.run_psql(
        "postgres",
        f"UPDATE pg_database SET datistemplate = false WHERE datname = {safe_name};",
    )
