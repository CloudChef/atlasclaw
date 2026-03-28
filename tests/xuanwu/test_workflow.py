# -*- coding: utf-8 -*-
"""
工作流模块单元测试

测试 WorkflowEngine、AgentOrchestrator 等组件。
"""

import asyncio

import pytest
from pydantic import BaseModel

from app.xuanwu.workflow.engine import (
    WorkflowEngine,
    StepStatus,
    WorkflowError,
)
from app.xuanwu.workflow.orchestrator import (
    AgentDefinition,
    Task,
    TaskStatus,
    SimpleOrchestrator,
)


class SimpleState(BaseModel):
    """简单状态模型"""
    count: int = 0
    message: str = ""
    data: list[str] = []


class TestWorkflowEngine:
    """WorkflowEngine 测试类"""
    
    def test_create_engine(self):
        """测试创建引擎"""
        engine = WorkflowEngine[SimpleState]()
        assert engine is not None
        
    @pytest.mark.asyncio
    async def test_single_step_workflow(self):
        """测试单步骤工作流"""
        engine = WorkflowEngine[SimpleState]()
        
        @engine.step()
        async def increment(state: SimpleState) -> SimpleState:
            state.count += 1
            return state
            
        result = await engine.run(SimpleState())
        
        assert result.count == 1
        
    @pytest.mark.asyncio
    async def test_sequential_steps(self):
        """测试顺序执行步骤"""
        engine = WorkflowEngine[SimpleState]()
        
        @engine.step()
        async def step1(state: SimpleState) -> SimpleState:
            state.data.append("step1")
            return state
            
        @engine.step(after=["step1"])
        async def step2(state: SimpleState) -> SimpleState:
            state.data.append("step2")
            return state
            
        @engine.step(after=["step2"])
        async def step3(state: SimpleState) -> SimpleState:
            state.data.append("step3")
            return state
            
        result = await engine.run(SimpleState())
        
        assert result.data == ["step1", "step2", "step3"]
        
    @pytest.mark.asyncio
    async def test_step_failure(self):
        """测试步骤失败"""
        engine = WorkflowEngine[SimpleState]()
        
        @engine.step()
        async def failing_step(state: SimpleState) -> SimpleState:
            raise ValueError("Intentional failure")
            
        with pytest.raises(WorkflowError):
            await engine.run(SimpleState())
            
    def test_get_step_status(self):
        """测试获取步骤状态"""
        engine = WorkflowEngine[SimpleState]()
        
        @engine.step()
        async def test_step(state: SimpleState) -> SimpleState:
            return state
            
        status = engine.get_status()
        
        assert "test_step" in status
        assert status["test_step"] == StepStatus.PENDING
        
    @pytest.mark.asyncio
    async def test_topological_sort(self):
        """测试拓扑排序"""
        engine = WorkflowEngine[SimpleState]()
        
        @engine.step(after=["step2"])
        async def step3(state: SimpleState) -> SimpleState:
            state.data.append("3")
            return state
            
        @engine.step()
        async def step1(state: SimpleState) -> SimpleState:
            state.data.append("1")
            return state
            
        @engine.step(after=["step1"])
        async def step2(state: SimpleState) -> SimpleState:
            state.data.append("2")
            return state
            
        result = await engine.run(SimpleState())
        
        # 应该按依赖顺序执行
        assert result.data == ["1", "2", "3"]
        
    @pytest.mark.asyncio
    async def test_circular_dependency_detection(self):
        """测试循环依赖检测"""
        engine = WorkflowEngine[SimpleState]()
        
        @engine.step(after=["step_b"])
        async def step_a(state: SimpleState) -> SimpleState:
            return state
            
        @engine.step(after=["step_a"])
        async def step_b(state: SimpleState) -> SimpleState:
            return state
            
        with pytest.raises(WorkflowError):
            await engine.run(SimpleState())
            
    def test_reset_engine(self):
        """测试重置引擎"""
        engine = WorkflowEngine[SimpleState]()
        
        @engine.step()
        async def test_step(state: SimpleState) -> SimpleState:
            return state
            
        engine.reset()
        status = engine.get_status()
        
        assert status["test_step"] == StepStatus.PENDING


class TestAgentOrchestrator:
    """AgentOrchestrator 测试类"""
    
    def test_create_orchestrator(self):
        """测试创建编排器"""
        orchestrator = SimpleOrchestrator()
        assert orchestrator is not None
        
    def test_register_agent(self):
        """测试注册 Agent"""
        orchestrator = SimpleOrchestrator()
        
        agent = AgentDefinition(
            id="researcher",
            role="Research Analyst",
            goal="Conduct comprehensive research"
        )
        
        orchestrator.register_agent(agent)
        
        retrieved = orchestrator.get_agent("researcher")
        assert retrieved is not None
        assert retrieved.role == "Research Analyst"
        
    def test_unregister_agent(self):
        """测试注销 Agent"""
        orchestrator = SimpleOrchestrator()
        
        agent = AgentDefinition(id="test", role="Test", goal="Test")
        orchestrator.register_agent(agent)
        
        success = orchestrator.unregister_agent("test")
        assert success
        
        retrieved = orchestrator.get_agent("test")
        assert retrieved is None
        
    def test_list_agents(self):
        """测试列出所有 Agent"""
        orchestrator = SimpleOrchestrator()
        
        orchestrator.register_agent(AgentDefinition(id="a1", role="Role1", goal="Goal1"))
        orchestrator.register_agent(AgentDefinition(id="a2", role="Role2", goal="Goal2"))
        
        agents = orchestrator.list_agents()
        
        assert len(agents) == 2
        
    @pytest.mark.asyncio
    async def test_sequential_executes_and_passes_previous_output(self):
        """测试顺序执行会把上一步输出作为后续上下文。"""

        async def fake_executor(agent, task, context):
            return f"{agent.id}:{task.id}:ctx={len(context)}"

        orchestrator = SimpleOrchestrator(executor=fake_executor)
        orchestrator.register_agent(AgentDefinition(id="a1", role="分析", goal="分析问题"))
        orchestrator.register_agent(AgentDefinition(id="a2", role="总结", goal="总结输出"))

        tasks = [
            Task(id="t1", description="分析告警", agent_id="a1"),
            Task(id="t2", description="整理结论", agent_id="a2"),
        ]

        results = await orchestrator.sequential(tasks)

        assert [r.status for r in results] == [TaskStatus.COMPLETED, TaskStatus.COMPLETED]
        assert "ctx=0" in (results[0].output or "")
        assert "ctx=1" in (results[1].output or "")
            
    @pytest.mark.asyncio
    async def test_parallel_executes_and_keeps_input_order(self):
        """测试并行执行后结果顺序仍与输入任务一致。"""

        async def fake_executor(agent, task, context):
            if task.id == "t1":
                await asyncio.sleep(0.02)
            return f"{agent.id}:{task.id}"

        orchestrator = SimpleOrchestrator(executor=fake_executor)
        orchestrator.register_agent(AgentDefinition(id="a1", role="R1", goal="G1"))
        orchestrator.register_agent(AgentDefinition(id="a2", role="R2", goal="G2"))

        tasks = [
            Task(id="t1", description="task1", agent_id="a1"),
            Task(id="t2", description="task2", agent_id="a2"),
        ]
        results = await orchestrator.parallel(tasks, max_concurrency=2)

        assert [r.task_id for r in results] == ["t1", "t2"]
        assert all(r.status == TaskStatus.COMPLETED for r in results)
            
    @pytest.mark.asyncio
    async def test_delegate_selects_best_match_agent(self):
        """测试智能委派会按 role/goal/skills 匹配最合适 Agent。"""
        orchestrator = SimpleOrchestrator()
        orchestrator.register_agent(
            AgentDefinition(id="writer", role="报告撰写", goal="输出巡检报告", skills=["报告", "文档"])
        )
        orchestrator.register_agent(
            AgentDefinition(id="ops", role="故障排查", goal="处理线上告警", skills=["告警", "排查"])
        )

        result = await orchestrator.delegate(Task(id="t1", description="请撰写巡检报告"))

        assert result.status == TaskStatus.COMPLETED
        assert result.agent_id == "writer"

    @pytest.mark.asyncio
    async def test_hierarchical_assigns_worker_with_manager_metadata(self):
        """测试层级执行会将任务分配给最匹配 worker，并带上 manager 元信息。"""
        orchestrator = SimpleOrchestrator()
        orchestrator.register_agent(AgentDefinition(id="mgr", role="经理", goal="分派任务"))
        orchestrator.register_agent(AgentDefinition(id="ops", role="故障排查", goal="处理告警", skills=["数据库", "告警"]))
        orchestrator.register_agent(AgentDefinition(id="doc", role="文档整理", goal="输出文档", skills=["文档", "总结"]))

        tasks = [
            Task(id="db", description="排查数据库连接异常"),
            Task(id="summary", description="整理发布文档"),
        ]

        results = await orchestrator.hierarchical(tasks, manager_id="mgr", workers=["ops", "doc"])

        assert [r.agent_id for r in results] == ["ops", "doc"]
        assert all(r.metadata.get("manager_id") == "mgr" for r in results)

    @pytest.mark.asyncio
    async def test_sequential_stop_on_error(self):
        """测试 stop_on_error=True 时遇错会中断后续任务。"""

        async def fake_executor(agent, task, context):
            if task.id == "t1":
                raise RuntimeError("boom")
            return "ok"

        orchestrator = SimpleOrchestrator(executor=fake_executor)
        orchestrator.register_agent(AgentDefinition(id="a1", role="R1", goal="G1"))
        tasks = [
            Task(id="t1", description="first", agent_id="a1"),
            Task(id="t2", description="second", agent_id="a1"),
        ]

        results = await orchestrator.sequential(tasks, stop_on_error=True)

        assert len(results) == 1
        assert results[0].status == TaskStatus.FAILED


class TestAgentDefinition:
    """AgentDefinition 测试类"""
    
    def test_create_agent_definition(self):
        """测试创建 Agent 定义"""
        agent = AgentDefinition(
            id="analyst",
            role="Data Analyst",
            goal="Analyze data patterns",
            backstory="Expert in data analysis",
            skills=["query_database", "visualize_data"],
            llm_model="gpt-4o"
        )
        
        assert agent.id == "analyst"
        assert agent.role == "Data Analyst"
        assert len(agent.skills) == 2
        
    def test_agent_definition_defaults(self):
        """测试 Agent 定义默认值"""
        agent = AgentDefinition(
            id="basic",
            role="Basic Agent",
            goal="Do basic tasks"
        )
        
        assert agent.backstory == ""
        assert agent.skills == []
        assert agent.llm_model == "gpt-4o"
        assert agent.max_iterations == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
