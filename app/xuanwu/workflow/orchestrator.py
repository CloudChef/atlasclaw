"""Multi-agent task orchestration primitives."""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field


class TaskStatus(Enum):
    """Execution status for an orchestrated task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionMode(Enum):
    """Supported orchestration modes."""

    SEQUENTIAL = "sequential"  # sequential execution
    PARALLEL = "parallel"  # parallel execution
    DELEGATE = "delegate"  # Assign a task to the most suitable agent.
    HIERARCHICAL = "hierarchical"  # Manager-worker orchestration.


@dataclass
class AgentDefinition:
    """Definition of an orchestrated agent."""

    id: str
    role: str
    goal: str
    backstory: str = ""
    skills: list[str] = field(default_factory=list)
    llm_model: str = "gpt-4o"
    max_iterations: int = 10
    verbose: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskResult(BaseModel):
    """Result returned for an executed task."""

    agent_id: str
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    output: Optional[str] = None
    error: Optional[str] = None
    tokens_used: int = 0
    duration_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class Task:
    """Task definition passed into the orchestrator."""

    id: str
    description: str
    expected_output: str = ""
    agent_id: Optional[str] = None
    context: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    async_execution: bool = False


class AgentOrchestrator(ABC):
    """Abstract base class for multi-agent orchestration backends."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        self._results: dict[str, TaskResult] = {}

    def register_agent(self, agent: AgentDefinition) -> None:
        """Register an agent definition."""
        self._agents[agent.id] = agent

    def unregister_agent(self, agent_id: str) -> bool:
        """Remove an agent definition by ID."""
        if agent_id in self._agents:
            del self._agents[agent_id]
            return True
        return False

    def get_agent(self, agent_id: str) -> Optional[AgentDefinition]:
        """Return an agent definition by ID."""
        return self._agents.get(agent_id)

    def list_agents(self) -> list[AgentDefinition]:
        """Return all registered agents."""
        return list(self._agents.values())

    @abstractmethod
    async def sequential(
        self,
        tasks: list[Task],
        *,
        stop_on_error: bool = True,
    ) -> list[TaskResult]:
        """Run tasks sequentially."""
        ...

    @abstractmethod
    async def parallel(
        self,
        tasks: list[Task],
        *,
        max_concurrency: int = 5,
    ) -> list[TaskResult]:
        """Run tasks in parallel."""
        ...

    @abstractmethod
    async def delegate(
        self,
        task: Task,
        *,
        candidates: Optional[list[str]] = None,
    ) -> TaskResult:
        """Delegate a single task to one selected agent."""
        ...

    @abstractmethod
    async def hierarchical(
        self,
        tasks: list[Task],
        *,
        manager_id: str,
        workers: list[str],
    ) -> list[TaskResult]:
        """Run tasks in hierarchical manager-worker mode."""
        ...

    def get_result(self, task_id: str) -> Optional[TaskResult]:
        """Return a previously stored task result."""
        return self._results.get(task_id)

    def clear_results(self) -> None:
        """Clear all stored task results."""
        self._results.clear()


class SimpleOrchestrator(AgentOrchestrator):
    """Reference orchestrator implementation for early runtime phases."""

    def __init__(
        self,
        executor: Optional[Callable[[AgentDefinition, Task, list[str]], Any]] = None,
    ) -> None:
        super().__init__()
        self._executor = executor

    async def sequential(
        self,
        tasks: list[Task],
        *,
        stop_on_error: bool = True,
    ) -> list[TaskResult]:
        results: list[TaskResult] = []
        shared_context: list[str] = []

        for task in tasks:
            agent = self._resolve_agent_for_task(task)
            result = await self._execute_task(
                task=task,
                agent=agent,
                inherited_context=shared_context,
                metadata={"mode": ExecutionMode.SEQUENTIAL.value},
            )
            results.append(result)

            if result.status == TaskStatus.COMPLETED and result.output:
                shared_context.append(result.output)

            if result.status == TaskStatus.FAILED and stop_on_error:
                break

        return results

    async def parallel(
        self,
        tasks: list[Task],
        *,
        max_concurrency: int = 5,
    ) -> list[TaskResult]:
        if not tasks:
            return []

        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async def run_one(index: int, task: Task) -> tuple[int, TaskResult]:
            async with semaphore:
                agent = self._resolve_agent_for_task(task)
                result = await self._execute_task(
                    task=task,
                    agent=agent,
                    metadata={"mode": ExecutionMode.PARALLEL.value},
                )
                return index, result

        pairs = await asyncio.gather(*(run_one(i, t) for i, t in enumerate(tasks)))
        pairs.sort(key=lambda item: item[0])
        return [result for _, result in pairs]

    async def delegate(
        self,
        task: Task,
        *,
        candidates: Optional[list[str]] = None,
    ) -> TaskResult:
        agent = self._select_agent_for_task(task, candidates=candidates)
        return await self._execute_task(
            task=task,
            agent=agent,
            metadata={
                "mode": ExecutionMode.DELEGATE.value,
                "candidate_count": len(candidates or self._agents),
            },
        )

    async def hierarchical(
        self,
        tasks: list[Task],
        *,
        manager_id: str,
        workers: list[str],
    ) -> list[TaskResult]:
        manager = self.get_agent(manager_id)
        if manager is None:
            return [
                self._store_result(
                    TaskResult(
                        agent_id=manager_id,
                        task_id=task.id,
                        status=TaskStatus.FAILED,
                        error=f"manager_not_found: {manager_id}",
                        metadata={"mode": ExecutionMode.HIERARCHICAL.value},
                    )
                )
                for task in tasks
            ]

        available_workers = [w for w in workers if w in self._agents]
        if not available_workers:
            return [
                self._store_result(
                    TaskResult(
                        agent_id="",
                        task_id=task.id,
                        status=TaskStatus.FAILED,
                        error="no_available_workers",
                        metadata={
                            "mode": ExecutionMode.HIERARCHICAL.value,
                            "manager_id": manager_id,
                        },
                    )
                )
                for task in tasks
            ]

        results: list[TaskResult] = []
        manager_context = [f"manager_role={manager.role}", f"manager_goal={manager.goal}"]

        for task in tasks:
            worker = self._select_agent_for_task(task, candidates=available_workers)
            result = await self._execute_task(
                task=task,
                agent=worker,
                inherited_context=manager_context,
                metadata={
                    "mode": ExecutionMode.HIERARCHICAL.value,
                    "manager_id": manager_id,
                    "assigned_by": manager_id,
                },
            )
            results.append(result)

        return results

    def _resolve_agent_for_task(self, task: Task) -> Optional[AgentDefinition]:
        if task.agent_id:
            return self.get_agent(task.agent_id)
        return self._select_agent_for_task(task)

    def _select_agent_for_task(
        self,
        task: Task,
        *,
        candidates: Optional[list[str]] = None,
    ) -> Optional[AgentDefinition]:
        if not self._agents:
            return None

        candidate_ids = candidates or list(self._agents.keys())
        pool = [self._agents[aid] for aid in candidate_ids if aid in self._agents]
        if not pool:
            return None

        if task.agent_id:
            for agent in pool:
                if agent.id == task.agent_id:
                    return agent

        scored = sorted(
            pool,
            key=lambda agent: (-self._score_agent(task, agent), agent.id),
        )
        return scored[0]

    def _score_agent(self, task: Task, agent: AgentDefinition) -> int:
        task_text = " ".join([task.description, task.expected_output, *task.context]).lower()
        profile_text = " ".join([agent.role, agent.goal, *agent.skills]).lower()

        score = 0
        terms = [agent.role, agent.goal, *agent.skills]

        # item in(in and)
        for term in terms:
            term_text = (term or "").strip().lower()
            if term_text and term_text in task_text:
                score += 5

        # token count
        task_tokens = set(self._tokenize(task_text))
        profile_tokens = set(self._tokenize(profile_text))
        score += len(task_tokens & profile_tokens)

        return score

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower())

    async def _execute_task(
        self,
        *,
        task: Task,
        agent: Optional[AgentDefinition],
        inherited_context: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TaskResult:
        start = time.monotonic()

        if agent is None:
            return self._store_result(
                TaskResult(
                    agent_id=task.agent_id or "",
                    task_id=task.id,
                    status=TaskStatus.FAILED,
                    error="no_available_agent",
                    metadata=metadata or {},
                )
            )

        merged_context = list(task.context)
        if inherited_context:
            merged_context.extend(inherited_context)

        result = TaskResult(
            agent_id=agent.id,
            task_id=task.id,
            status=TaskStatus.RUNNING,
            metadata={"context_size": len(merged_context), **(metadata or {})},
        )

        try:
            output = await self._invoke_executor(agent, task, merged_context)
            result.output = output
            result.status = TaskStatus.COMPLETED
            result.tokens_used = max(1, len(output) // 4) if output else 0
        except Exception as exc:  # noqa:BLE001 - return failed
            result.status = TaskStatus.FAILED
            result.error = str(exc)
        finally:
            result.duration_ms = int((time.monotonic() - start) * 1000)

        return self._store_result(result)

    async def _invoke_executor(
        self,
        agent: AgentDefinition,
        task: Task,
        merged_context: list[str],
    ) -> str:
        if self._executor:
            value = self._executor(agent, task, merged_context)
            if inspect.isawaitable(value):
                value = await value
            return "" if value is None else str(value)

        parts = [f"[{agent.role}] {task.description}"]
        if task.expected_output:
            parts.append(f"expected={task.expected_output}")
        if merged_context:
            parts.append(f"context={len(merged_context)}")
        return " | ".join(parts)

    def _store_result(self, result: TaskResult) -> TaskResult:
        self._results[result.task_id] = result
        return result
