"""Odoo-specific filestore operations.

Odoo stores attachments in /opt/odoo/odoo-data/filestore/<db_name>/.
"""

from __future__ import annotations

from ...core.docker import docker_cp_to, docker_exec
from ...core.sanitize import validate_db_name

FILESTORE_BASE = "/opt/odoo/odoo-data/filestore"
FILESTORE_OWNER = "odoo:odoo"


def restore(container: str, db_name: str, src_dir: str) -> None:
    """Copy filestore from host temp dir into the Odoo container."""
    validate_db_name(db_name)
    dst = f"{FILESTORE_BASE}/{db_name}"
    docker_exec(container, ["mkdir", "-p", "--", dst], timeout=10)
    docker_cp_to(container, f"{src_dir}/.", dst)
    docker_exec(
        container,
        ["chown", "-R", FILESTORE_OWNER, FILESTORE_BASE],
        timeout=30,
    )


def remove(container: str, db_name: str) -> None:
    """Remove the filestore directory for a database."""
    validate_db_name(db_name)
    dst = f"{FILESTORE_BASE}/{db_name}"
    docker_exec(
        container,
        ["rm", "-rf", "--", dst],
        timeout=30,
    )


def copy(container: str, src_db: str, dst_db: str) -> None:
    """Copy filestore from one database to another inside the container."""
    validate_db_name(src_db)
    validate_db_name(dst_db)
    src_path = f"{FILESTORE_BASE}/{src_db}"
    dst_path = f"{FILESTORE_BASE}/{dst_db}"
    # Check if source exists, then copy. Using list-form commands to avoid
    # shell injection (no bash -c with interpolated strings).
    result = docker_exec(
        container,
        ["test", "-d", src_path],
        timeout=10,
    )
    if result.returncode == 0:
        # Safe because Odoo's filestore is immutable (content-addressable, never modified
        # in-place); deletions only unlink, so each DB's references are independent.
        r = docker_exec(
            container,
            ["cp", "-al", "--", src_path, dst_path],
            timeout=30,
        )
        if r.returncode != 0:
            # Fallback: cross-filesystem or kernel without hardlink support
            docker_exec(
                container,
                ["cp", "-a", "--", src_path, dst_path],
                timeout=120,
            )