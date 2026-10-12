"""A real MCP round trip; unavailable context is never filled in from memory."""

import json
import os
from pathlib import Path
import sys

from ..indexing.corpus import DEFAULT_PROJECT_ROOT


class ProjectMcpClient:
    def __init__(self, project_root: Path = DEFAULT_PROJECT_ROOT, timeout: float = 12):
        self.project_root = Path(project_root).resolve()
        self.timeout = timeout

    async def _read_branch(self) -> dict:
        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        environment = dict(os.environ)
        environment["PROJECT_ROOT"] = str(self.project_root)
        environment["PYTHONPATH"] = str(DEFAULT_PROJECT_ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "app.project_help.mcp_server"], env=environment,
        )
        with anyio.fail_after(self.timeout):
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("git_branch", {})
                    if result.isError:
                        raise ValueError("git_branch завершился ошибкой")
                    value = result.structuredContent
                    if value is None:
                        value = json.loads(next(item.text for item in result.content if item.type == "text"))
                    if not isinstance(value, dict) or not {"branch", "commit", "detached", "repository", "observed_at"} <= value.keys():
                        raise ValueError("Некорректный ответ git_branch")
                    if value["branch"] is not None and not isinstance(value["branch"], str):
                        raise ValueError("Некорректная ветка")
                    return value

    def branch(self) -> dict:
        try:
            import anyio
            value = anyio.run(self._read_branch)
            return {"tool": "git_branch", "transport": "stdio", "status": "ok", **value}
        except Exception:
            return {"tool": "git_branch", "transport": "stdio", "status": "unavailable",
                    "error": "MCP недоступен: проверьте зависимости, Git и PROJECT_ROOT"}
