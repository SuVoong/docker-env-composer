"""Textual TUI entry points."""

from __future__ import annotations

from .screens.create import CreateWizardApp
from .screens.dashboard import DashboardApp
from .screens.select_env import SelectEnvApp


def run_create_wizard(
    env: dict,
    name: str | None = None,
    description: str | None = None,
    template: str | None = None,
    ttl: int | None = None,
    modules: str | None = None,
) -> None:
    """Launch the create wizard TUI."""
    app = CreateWizardApp(env, name, description, template, ttl, modules)
    app.run()


def run_dashboard(env: dict) -> None:
    """Launch the dashboard TUI (includes drop via 'd' key)."""
    app = DashboardApp(env)
    app.run()


def run_env_selector() -> dict | None:
    """Launch the environment selector TUI.

    Returns the selected environment dict, or None if the user cancelled.
    """
    app = SelectEnvApp()
    app.run()
    return app.selected_env
