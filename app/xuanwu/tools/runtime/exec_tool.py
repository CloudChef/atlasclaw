"""


exec execute tool

Core tool that supports multi-step execution and environment self-healing.
Executes shell commands with `asyncio.create_subprocess_shell`,
capturing `stdout`/`stderr`/`exitCode`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult
from app.xuanwu.tools.truncation import truncate_output
from app.xuanwu.tools.work_dir_guard import resolve_cwd

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


async def exec_tool(
    ctx: "RunContext[SkillDeps]",
    command: str,
    timeout_ms: int = 30000,
    cwd: Optional[str] = None,
) -> dict:
    """

execute shell

    Args:
        ctx:PydanticAI RunContext dependency injection
        command:execute command string
        timeout_ms:count(default 30000)
        cwd:working directory(optional)

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    start = time.monotonic()
    status = "completed"
    exit_code = 0
    output = ""
    effective_cwd = ""

    try:
        effective_cwd = str(resolve_cwd(ctx, cwd))
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=effective_cwd,
        )

        timeout_s = timeout_ms / 1000.0
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
            output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            exit_code = proc.returncode or 0
            status = "completed" if exit_code == 0 else "failed"
        except asyncio.TimeoutError:
            proc.kill()
            try:
                stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
                output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            except Exception:
                output = ""
            status = "timeout"
            exit_code = -1

    except Exception as e:
        output = str(e)
        status = "failed"
        exit_code = -1

    duration_ms = int((time.monotonic() - start) * 1000)

    # truncate context overflow
    output = truncate_output(output)

    result = ToolResult(
        content=[{"type": "text", "text": output}],
        details={
            "status": status,
            "exitCode": exit_code,
            "durationMs": duration_ms,
            "cwd": effective_cwd,
        },
        is_error=(status != "completed"),
    )
    return result.to_dict()
