"""Auto-detect Docker environment from docker-compose.yml using ERP plugins."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


# Registry of available plugins (ordered by priority: specific first, generic last)
_PLUGIN_CLASSES: list[type] = []


def _load_plugins() -> list[type]:
    """Lazily load plugin classes."""
    if not _PLUGIN_CLASSES:
        from ..plugins.odoo import OdooPlugin
        from ..plugins.generic import GenericPlugin
        _PLUGIN_CLASSES.extend([OdooPlugin, GenericPlugin])
    return _PLUGIN_CLASSES


def find_compose_file(start_dir: Path | None = None) -> Path | None:
    """Walk up from start_dir looking for docker-compose.yml."""
    current = start_dir or Path.cwd()
    for _ in range(5):
        for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
            candidate = current / name
            if candidate.is_file():
                return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def parse_compose(compose_path: Path) -> dict | None:
    """Detect ERP type and extract environment from docker-compose.yml.

    Tries each registered plugin in order. Returns the first match.
    """
    with open(compose_path) as f:
        data = yaml.safe_load(f)

    services = data.get("services", {})

    for plugin_cls in _load_plugins():
        env = plugin_cls.detect_from_compose(services)
        if env is not None:
            compose_dir = compose_path.parent
            env["compose_dir"] = str(compose_dir)
            env["templates_dir"] = str(compose_dir / "templates")
            return env

    return None


def get_plugin_for_env(env: dict):
    """Instantiate the correct plugin based on env['erp_plugin']."""
    plugin_name = env.get("erp_plugin", "generic")
    for plugin_cls in _load_plugins():
        instance = plugin_cls()
        if instance.name == plugin_name:
            return instance
    # Fallback to generic
    from ..plugins.generic import GenericPlugin
    return GenericPlugin()


def check_container_running(container_name: str) -> bool:
    """Check if a Docker container is running."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name=^{container_name}$"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return container_name in result.stdout.strip().split("\n")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def detect_environment(start_dir: Path | None = None) -> dict | None:
    """Full detection: find compose, detect ERP via plugins, check containers.

    Returns environment dict with extra 'status' fields, or None.
    """
    compose_path = find_compose_file(start_dir)
    if not compose_path:
        return None

    env = parse_compose(compose_path)
    if not env:
        return None

    env["compose_file"] = str(compose_path)
    _enrich_with_status(env)
    return env


def _enrich_with_status(env: dict) -> None:
    """Check container status and add runtime fields to env dict."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as executor:
        f_app = executor.submit(check_container_running, env.get("odoo_container", ""))
        f_pg = executor.submit(check_container_running, env.get("pg_container", ""))
        env["app_running"] = f_app.result()
        env["pg_running"] = f_pg.result()
    # Backward compat alias
    env["odoo_running"] = env["app_running"]


def detect_from_path(path: str) -> dict | None:
    """Detect a Docker environment from a user-provided path.

    Accepts a directory (looks for docker-compose.yml inside) or a
    direct path to a compose file.  Returns env dict or None.
    """
    p = Path(path).expanduser().resolve()

    if p.is_file():
        compose_path = p
    elif p.is_dir():
        compose_path = find_compose_file(p)
        if not compose_path:
            return None
    else:
        return None

    env = parse_compose(compose_path)
    if env is None:
        return None

    env["compose_file"] = str(compose_path)
    env["display_name"] = p.name if p.is_file() else p.name
    # Use parent dir name, skipping 'docker' if present
    parts = []
    for part in compose_path.parent.parts:
        if part == "/":
            continue
        parts.append(part)
    # Use last 2 meaningful parts as display name
    meaningful = [p for p in parts[-3:] if p.lower() != "docker"]
    env["display_name"] = "/".join(meaningful) if meaningful else compose_path.parent.name

    return env
