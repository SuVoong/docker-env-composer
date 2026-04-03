"""Docker container operations (ERP-agnostic).

Only generic Docker primitives. ERP-specific operations (filestore,
module install) live in plugins/.
"""

from __future__ import annotations

import subprocess


def docker_exec(
    container: str,
    command: list[str],
    timeout: int = 600,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Execute a command inside a Docker container."""
    cmd = ["docker", "exec", "-i", container, *command]
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def docker_cp_to(container: str, src: str, dst: str) -> None:
    """Copy file/dir from host to container."""
    result = subprocess.run(
        ["docker", "cp", src, f"{container}:{dst}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker cp failed: {result.stderr}")
