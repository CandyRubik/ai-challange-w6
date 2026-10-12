"""Run with python -m app.project_help.mcp_server (MCP stdio transport)."""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess

from mcp.server.fastmcp import FastMCP

from ..indexing.corpus import DEFAULT_PROJECT_ROOT

server = FastMCP("project-context", log_level="ERROR")


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", f"safe.directory={root}", "-C", str(root), *arguments], capture_output=True,
                          text=True, timeout=5, check=False)


@server.tool()
def git_branch() -> dict:
    """Read the current branch and HEAD of the configured project checkout."""
    root = Path(os.getenv("PROJECT_ROOT", str(DEFAULT_PROJECT_ROOT))).resolve()
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode or Path(top.stdout.strip()).resolve() != root:
        raise ValueError("В настроенном каталоге нет git-checkout проекта")
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if branch.returncode not in (0, 1):
        raise ValueError("Не удалось прочитать текущую ветку")
    return {
        "repository": root.name,
        "branch": branch.stdout.strip() or None,
        "commit": head.stdout.strip() if head.returncode == 0 else None,
        "detached": branch.returncode == 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    server.run(transport="stdio")
