"""Step-based workflow engine for multi-step execution."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Coroutine,
    Generic,
    Optional,
    TypeVar,
)

from pydantic import BaseModel


class StepStatus(Enum):
    """Execution status for a workflow step."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


T = TypeVar("T", bound=BaseModel)

# Workflow step handler type.
StepHandler = Callable[[T], Coroutine[Any, Any, T]]
# Router function type used for conditional branching.
RouterFunc = Callable[[T], Coroutine[Any, Any, Optional[str]]]


@dataclass
class WorkflowStep:
    """Definition and runtime state for a workflow step."""
    name: str
    handler: StepHandler[Any]
    after: list[str] = field(default_factory=list)
    router: Optional[RouterFunc[Any]] = None
    status: StepStatus = StepStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    
    def reset(self) -> None:
        """Reset transient execution state for the step."""
        self.status = StepStatus.PENDING
        self.result = None
        self.error = None


class WorkflowEngine(Generic[T]):
    """Execute multi-step workflows with dependencies and routing.

    Example:
    
        class MyState(BaseModel):
            count:int = 0
            message:str = ""
            
        engine = WorkflowEngine[MyState]()
        
        @engine.step()
        async def step1(state:MyState) -> MyState:
            state.count += 1
            return state
            
        @engine.step(after=["step1"])
        async def step2(state:MyState) -> MyState:
            state.message = f"Count is {state.count}"
            return state
            
        @engine.router(after="step2")
        async def route_decision(state:MyState) -> Optional[str]:
            if state.count > 5:
                return "step3"
            return None
            
        result = await engine.run(MyState())
    """
    
    def __init__(self) -> None:
        """Initialize an empty workflow engine."""
        self._steps: dict[str, WorkflowStep] = {}
        self._routers: dict[str, RouterFunc[T]] = {}
        self._active_routes: set[str] = set()
        self._execution_order: list[str] = []
        self._state: Optional[T] = None
        
    def step(
        self,
        name: Optional[str] = None,
        *,
        after: Optional[list[str]] = None,
    ) -> Callable[[StepHandler[T]], StepHandler[T]]:
        """Register a workflow step decorator.

        Args:
            name: Optional explicit step name.
            after: Prerequisite step names.

        Returns:
            A decorator that registers the step handler.
        """
        def decorator(func: StepHandler[T]) -> StepHandler[T]:
            step_name = name or func.__name__
            self._steps[step_name] = WorkflowStep(
                name=step_name,
                handler=func,
                after=after or []
            )
            return func
        return decorator
        
    def router(
        self,
        name: Optional[str] = None,
        *,
        after: Optional[str] = None,
    ) -> Callable[[RouterFunc[T]], RouterFunc[T]]:
        """Register a router decorator for conditional branching.

        Args:
            name: Optional explicit router name.
            after: Step name after which this router should run.

        Returns:
            A decorator that registers the router function.
        """
        def decorator(func: RouterFunc[T]) -> RouterFunc[T]:
            router_name = name or func.__name__
            self._routers[router_name] = func
            # Attach the router to the target step when provided.
            if after and after in self._steps:
                self._steps[after].router = func
            return func
        return decorator
        
    def register_step(
        self,
        name: str,
        handler: StepHandler[T],
        *,
        after: Optional[list[str]] = None,
    ) -> None:
        """Register a workflow step programmatically."""
        self._steps[name] = WorkflowStep(
            name=name,
            handler=handler,
            after=after or []
        )
        
    def get_step(self, name: str) -> Optional[WorkflowStep]:
        """Return a registered step by name."""
        return self._steps.get(name)
        
    def get_all_steps(self) -> list[WorkflowStep]:
        """Return all registered steps."""
        return list(self._steps.values())
        
    async def run(
        self,
        initial_state: T,
        *,
        start_from: Optional[str] = None,
        active_routes: Optional[set[str]] = None,
    ) -> T:
        """Run the workflow and return the final state.

        Args:
            initial_state: Initial Pydantic state model.
            start_from: Optional step name used as the entry point.
            active_routes: Optional set of enabled route names.
        """
        self._state = initial_state
        self._active_routes = active_routes or set()
        
        # Reset step execution state before each run.
        for step in self._steps.values():
            step.reset()
            
        # Compute the execution order.
        self._execution_order = self._topological_sort()
        
        # 
        start_index = 0
        if start_from:
            if start_from not in self._steps:
                raise WorkflowError(f"Unknown step: {start_from}")
            try:
                start_index = self._execution_order.index(start_from)
            except ValueError:
                raise WorkflowError(f"Step not in execution order: {start_from}")
                
        # sequential executionstep
        for step_name in self._execution_order[start_index:]:
            step = self._steps[step_name]
            
            # check execute step
            if not self._should_execute_step(step):
                step.status = StepStatus.SKIPPED
                continue
                
            # executestep
            try:
                step.status = StepStatus.RUNNING
                self._state = await step.handler(self._state)
                step.status = StepStatus.COMPLETED
                step.result = self._state
                
                # execute
                if step.router:
                    next_step = await step.router(self._state)
                    if next_step:
                        self._active_routes.add(next_step)
                        
            except Exception as e:
                step.status = StepStatus.FAILED
                step.error = str(e)
                raise WorkflowError(f"Step '{step_name}' failed: {e}") from e
                
        return self._state
        
    def _topological_sort(self) -> list[str]:
        """calculateexecute"""
        # build
        in_degree: dict[str, int] = defaultdict(int)
        dependents: dict[str, list[str]] = defaultdict(list)
        
        for step in self._steps.values():
            if step.name not in in_degree:
                in_degree[step.name] = 0
            for dep in step.after:
                if dep not in self._steps:
                    raise WorkflowError(f"Unknown dependency: {dep} (required by {step.name})")
                dependents[dep].append(step.name)
                in_degree[step.name] += 1
                
        # Kahn
        queue = [name for name, deg in in_degree.items() if deg == 0]
        result: list[str] = []
        
        while queue:
            # name
            queue.sort()
            current = queue.pop(0)
            result.append(current)
            
            for dependent in dependents[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
                    
        # check
        if len(result) != len(self._steps):
            executed = set(result)
            remaining = [s for s in self._steps if s not in executed]
            raise WorkflowError(f"Circular dependency detected involving: {remaining}")
            
        return result
        
    def _should_execute_step(self, step: WorkflowStep) -> bool:
        """step execute"""
        # such as stepat in, execute
        if step.name in self._active_routes:
            return True
            
        # such as step, execute
        if not step.after:
            return True
            
        # check step
        for dep_name in step.after:
            dep_step = self._steps.get(dep_name)
            if not dep_step:
                return False
            if dep_step.status not in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                return False
                
        return True
        
    def reset(self) -> None:
        """work-Stream state"""
        for step in self._steps.values():
            step.reset()
        self._active_routes.clear()
        self._state = None
        
    def get_status(self) -> dict[str, StepStatus]:
        """get step"""
        return {name: step.status for name, step in self._steps.items()}
        

class WorkflowError(Exception):
    """work execute"""
    pass


# factory count
def create_workflow() -> WorkflowEngine[Any]:
    """create work instance"""
    return WorkflowEngine()
