# -*- coding: utf-8 -*-
"""Service operations for Agent configuration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.xuanwu.db.models import AgentModel
from app.xuanwu.db.schemas import AgentCreate, AgentUpdate, AgentResponse

logger = logging.getLogger(__name__)


class AgentConfigService:
    """Service operations for Agent configuration."""

    @staticmethod
    async def create(session: AsyncSession, agent_data: AgentCreate) -> AgentModel:
        """Create a new Agent.

        Args:
            session: Database session
            agent_data: Agent creation data

        Returns:
            Created Agent model
        """
        agent = AgentModel(
            name=agent_data.name,
            display_name=agent_data.display_name,
            identity=agent_data.identity,
            user=agent_data.user,
            soul=agent_data.soul,
            memory=agent_data.memory,
            is_active=agent_data.is_active,
        )
        session.add(agent)
        await session.flush()
        await session.refresh(agent)
        logger.info(f"Created agent: {agent.name} (id={agent.id})")
        return agent

    @staticmethod
    async def get_by_id(session: AsyncSession, agent_id: str) -> Optional[AgentModel]:
        """Get Agent by ID.

        Args:
            session: Database session
            agent_id: Agent ID

        Returns:
            Agent model or None
        """
        result = await session.execute(
            select(AgentModel).where(AgentModel.id == agent_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_name(session: AsyncSession, name: str) -> Optional[AgentModel]:
        """Get Agent by name.

        Args:
            session: Database session
            name: Agent name

        Returns:
            Agent model or None
        """
        result = await session.execute(
            select(AgentModel).where(AgentModel.name == name)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        session: AsyncSession,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[AgentModel], int]:
        """List all Agents with optional filtering.

        Args:
            session: Database session
            is_active: Filter by active status
            page: Page number
            page_size: Items per page

        Returns:
            Tuple of (list of agents, total count)
        """
        query = select(AgentModel)

        if is_active is not None:
            query = query.where(AgentModel.is_active == is_active)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = (await session.execute(count_query)).scalar()

        # Get paginated results
        query = query.offset((page - 1) * page_size).limit(page_size)
        query = query.order_by(AgentModel.created_at.desc())

        result = await session.execute(query)
        agents = list(result.scalars().all())

        return agents, total

    @staticmethod
    async def update(
        session: AsyncSession, agent_id: str, agent_data: AgentUpdate
    ) -> Optional[AgentModel]:
        """Update an Agent.

        Args:
            session: Database session
            agent_id: Agent ID
            agent_data: Update data

        Returns:
            Updated Agent model or None
        """
        agent = await AgentConfigService.get_by_id(session, agent_id)
        if agent is None:
            return None

        update_data = agent_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(agent, key, value)

        agent.updated_at = datetime.utcnow()
        await session.flush()
        await session.refresh(agent)

        logger.info(f"Updated agent: {agent.name} (id={agent.id})")
        return agent

    @staticmethod
    async def delete(session: AsyncSession, agent_id: str) -> bool:
        """Delete an Agent.

        Args:
            session: Database session
            agent_id: Agent ID

        Returns:
            True if deleted, False if not found
        """
        agent = await AgentConfigService.get_by_id(session, agent_id)
        if agent is None:
            return False

        await session.delete(agent)
        logger.info(f"Deleted agent: {agent.name} (id={agent.id})")
        return True

    @staticmethod
    async def upsert(session: AsyncSession, agent_data: AgentCreate) -> AgentModel:
        """Create or update Agent by name.

        Args:
            session: Database session
            agent_data: Agent data

        Returns:
            Created or updated Agent model
        """
        existing = await AgentConfigService.get_by_name(session, agent_data.name)
        if existing:
            update_data = AgentUpdate(**agent_data.model_dump())
            return await AgentConfigService.update(session, existing.id, update_data)
        else:
            return await AgentConfigService.create(session, agent_data)

    @staticmethod
    async def load_agent_config(session: AsyncSession, name: str) -> Optional[Dict[str, Any]]:
        """Load agent configuration for runtime use.

        Returns agent config in a format compatible with AgentLoader.

        Args:
            session: Database session
            name: Agent name

        Returns:
            Agent configuration dict or None
        """
        agent = await AgentConfigService.get_by_name(session, name)
        if agent is None:
            return None

        return {
            "id": agent.id,
            "name": agent.name,
            "display_name": agent.display_name,
            "identity": agent.identity or {},
            "user": agent.user or {},
            "soul": agent.soul or {},
            "memory": agent.memory or {},
            "is_active": agent.is_active,
        }
