"""Abstract base class for ERP plugins.

Each ERP (Odoo, ERPNext, Tryton, etc.) implements this interface to provide
ERP-specific behavior during template import and database management.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.postgres import PgConfig


class ERPPlugin(ABC):
    """Interface that ERP-specific plugins must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this ERP (e.g. 'odoo', 'erpnext', 'generic')."""

    @staticmethod
    @abstractmethod
    def detect_from_compose(services: dict) -> dict | None:
        """Detect this ERP from docker-compose services.

        Args:
            services: The 'services' dict from docker-compose.yml.

        Returns:
            Environment dict with ERP-specific keys, or None if not detected.
        """

    @abstractmethod
    def post_restore(
        self,
        container: str,
        db_name: str,
        pg: PgConfig,
        callback=None,
    ) -> dict:
        """Run ERP-specific steps after dump.sql restore.

        This is where schema sync, registry rebuild, etc. happen.

        Args:
            container: Docker container name.
            db_name: Database name.
            pg: PostgreSQL config.
            callback: Optional logging callback(msg: str).

        Returns:
            Summary dict with 'fixes_applied', 'details', etc.
        """

    @abstractmethod
    def install_modules(
        self,
        container: str,
        db_name: str,
        modules: list[str],
    ) -> None:
        """Install ERP modules/apps in the database.

        Args:
            container: Docker container name.
            db_name: Database name.
            modules: List of module names to install.
        """

    @abstractmethod
    def restore_filestore(
        self,
        container: str,
        db_name: str,
        src_dir: str,
    ) -> None:
        """Restore the filestore (attachments, media) into the container.

        Args:
            container: Docker container name.
            db_name: Database name.
            src_dir: Host path to the extracted filestore directory.
        """

    @abstractmethod
    def remove_filestore(self, container: str, db_name: str) -> None:
        """Remove the filestore for a database from the container."""

    @abstractmethod
    def copy_filestore(
        self,
        container: str,
        src_db: str,
        dst_db: str,
    ) -> None:
        """Copy filestore from one database to another inside the container."""

    @abstractmethod
    def neutralize(
        self,
        pg: PgConfig,
        db_name: str,
        callback=None,
    ) -> dict:
        """Neutralize runtime artifacts (crons, job queues, etc.) for dev use.

        Args:
            pg: PostgreSQL config.
            db_name: Database name.
            callback: Optional logging callback(msg: str).

        Returns:
            Summary dict with 'fixes_applied', 'details'.
        """
