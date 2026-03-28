"""

process managetool

Manage long-running background processes such as dev servers,
supporting the `start` / `poll` / `send_keys` / `kill` actions.
"""

from __future__ import annotations

import asyncio
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


@dataclass
class ManagedProcess:
    """"""

    process_id: str
    proc: asyncio.subprocess.Process
    command: str
    started_at: float = field(default_factory=time.monotonic)
    _buffer: str = ""
    _read_offset: int = 0

    async def read_incremental(self) -> str:
        """"""
        if self.proc.stdout is None:
            return ""

        # 
        chunks: list[str] = []
        while True:
            try:
                data = await asyncio.wait_for(self.proc.stdout.read(4096), timeout=0.1)
                if not data:
                    break
                chunks.append(data.decode("utf-8", errors="replace"))
            except asyncio.TimeoutError:
                break
            except Exception:
                break

        new_output = "".join(chunks)
        self._buffer += new_output
        result = self._buffer[self._read_offset :]
        self._read_offset = len(self._buffer)
        return result


class ProcessManager:
    """

manager

    management, Agent run.
    
"""

    def __init__(self) -> None:
        self._processes: dict[str, ManagedProcess] = {}

    async def start(self, command: str, cwd: Optional[str] = None) -> ManagedProcess:
        """"""
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        pid = f"proc_{uuid.uuid4().hex[:8]}"
        managed = ManagedProcess(process_id=pid, proc=proc, command=command)
        self._processes[pid] = managed
        return managed

    async def poll(self, process_id: str) -> Optional[str]:
        """get"""
        managed = self._processes.get(process_id)
        if not managed:
            return None
        return await managed.read_incremental()

    async def send_keys(self, process_id: str, text: str) -> bool:
        """"""
        managed = self._processes.get(process_id)
        if not managed or not managed.proc.stdin:
            return False
        try:
            managed.proc.stdin.write(text.encode("utf-8"))
            await managed.proc.stdin.drain()
            return True
        except Exception:
            return False

    async def kill(self, process_id: str) -> bool:
        """"""
        managed = self._processes.get(process_id)
        if not managed:
            return False
        try:
            managed.proc.kill()
            await managed.proc.wait()
        except ProcessLookupError:
            pass
        del self._processes[process_id]
        return True

    async def cleanup(self) -> int:
        """



        Returns:
            count
        
"""
        count = 0
        for pid in list(self._processes.keys()):
            await self.kill(pid)
            count += 1
        return count

    @property
    def active_count(self) -> int:
        return len(self._processes)


# (Agent-Runner run create instance)
_default_manager: Optional[ProcessManager] = None


def get_process_manager() -> ProcessManager:
    """get default manager"""
    global _default_manager
    if _default_manager is None:
        _default_manager = ProcessManager()
    return _default_manager


async def process_tool(
    ctx: "RunContext[SkillDeps]",
    action: str,
    command: Optional[str] = None,
    process_id: Optional[str] = None,
    text: Optional[str] = None,
    cwd: Optional[str] = None,
) -> dict:
    """

managetool

    Args:
        ctx:PydanticAI RunContext dependency injection
        action:action type(start / poll / send_keys / kill)
        command:command string(start required)
        process_id:ID(poll/send_keys/kill required)
        text:input text(send_keys required)
        cwd:working directory(start optional)

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    manager = get_process_manager()

    if action == "start":
        if not command:
            return ToolResult.error("command is required for start action").to_dict()
        managed = await manager.start(command, cwd=cwd)
        # etc.
        await asyncio.sleep(0.2)
        initial_output = await managed.read_incremental()
        return ToolResult(
            content=[{"type": "text", "text": initial_output}],
            details={
                "process_id": managed.process_id,
                "command": command,
                "status": "running",
            },
        ).to_dict()

    if action == "poll":
        if not process_id:
            return ToolResult.error("process_id is required for poll action").to_dict()
        output = await manager.poll(process_id)
        if output is None:
            return ToolResult.error(f"process {process_id} not found").to_dict()
        return ToolResult.text(
            output, details={"process_id": process_id, "status": "running"}
        ).to_dict()

    if action == "send_keys":
        if not process_id:
            return ToolResult.error("process_id is required for send_keys action").to_dict()
        if text is None:
            return ToolResult.error("text is required for send_keys action").to_dict()
        ok = await manager.send_keys(process_id, text)
        if not ok:
            return ToolResult.error(f"failed to send keys to process {process_id}").to_dict()
        return ToolResult.text(
            "keys sent", details={"process_id": process_id, "sent": text}
        ).to_dict()

    if action == "kill":
        if not process_id:
            return ToolResult.error("process_id is required for kill action").to_dict()
        ok = await manager.kill(process_id)
        if not ok:
            return ToolResult.error(f"process {process_id} not found").to_dict()
        return ToolResult.text(
            "process terminated", details={"process_id": process_id, "status": "killed"}
        ).to_dict()

    return ToolResult.error(f"unknown action: {action}").to_dict()
