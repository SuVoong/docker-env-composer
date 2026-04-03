"""Input sanitization for user-provided values.

All user inputs (CLI args, TUI widgets, compose file values) must be validated
before reaching SQL, subprocess, or filesystem operations.
"""

from __future__ import annotations

import re

# Database/template names: alphanumeric, underscore, hyphen, dot. Must start with letter or underscore.
_DB_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.\-]*$")

# Odoo module names: alphanumeric and underscore only.
_MODULE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Container names: alphanumeric, underscore, hyphen, dot.
_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")

MAX_DB_NAME_LENGTH = 63  # PostgreSQL limit


def validate_db_name(name: str) -> str:
    """Validate and return a safe database name.

    Raises ValueError if the name contains unsafe characters.
    """
    if not name:
        raise ValueError("Database name cannot be empty")
    if len(name) > MAX_DB_NAME_LENGTH:
        raise ValueError(
            f"Database name too long ({len(name)} chars, max {MAX_DB_NAME_LENGTH})"
        )
    if not _DB_NAME_RE.match(name):
        raise ValueError(
            f"Invalid database name '{name}'. "
            "Only letters, digits, underscores, hyphens, and dots are allowed. "
            "Must start with a letter or underscore."
        )
    return name

def validate_module_name(name: str) -> str:
    """Validate and return a safe Odoo module name.

    Raises ValueError if the name contains unsafe characters.
    """
    if not name:
        raise ValueError("Module name cannot be empty")
    if not _MODULE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid module name '{name}'. "
            "Only letters, digits, and underscores are allowed."
        )
    return name


def validate_module_list(modules: list[str]) -> list[str]:
    """Validate a list of module names."""
    return [validate_module_name(m) for m in modules]


def validate_container_name(name: str) -> str:
    """Validate and return a safe container name.

    Raises ValueError if the name contains unsafe characters.
    """
    if not name:
        raise ValueError("Container name cannot be empty")
    if not _CONTAINER_NAME_RE.match(name):
        raise ValueError(
            f"Invalid container name '{name}'. "
            "Only letters, digits, underscores, hyphens, and dots are allowed."
        )
    return name


def sanitize_sql_identifier(name: str) -> str:
    """Escape a SQL identifier for use in psql -c commands.

    This double-quotes the identifier and escapes any embedded double quotes.
    Use for database names in SQL queries passed to psql.
    """
    validate_db_name(name)
    # Double any existing double quotes (standard SQL escaping)
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def sanitize_sql_literal(value: str) -> str:
    """Escape a SQL string literal for use in psql -c commands.

    This single-quotes the value and escapes embedded single quotes
    using the '' convention (standard SQL).
    """
    validate_db_name(value)
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
