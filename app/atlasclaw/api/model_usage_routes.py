# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., All rights reserved.
"""Authenticated, current-user model usage queries."""
from __future__ import annotations

from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.auth.guards import get_current_user
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.db import get_db_session_dependency
from app.atlasclaw.db.models import ModelUsageModel

router = APIRouter(tags=["Model Usage"])


@router.get("/model-usage")
async def list_model_usage(
    model: Optional[str] = None,
    token_id: Optional[str] = None,
    model_config_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: UserInfo = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session_dependency),
) -> dict:
    """Paginate immutable call snapshots and aggregate the full filtered scope."""
    table = ModelUsageModel
    filters = [table.user_id == user.user_id]
    for column, value in ((table.model, model), (table.token_id, token_id),
                          (table.model_config_id, model_config_id)):
        if value is not None:
            filters.append(column == value)
    summary = (await session.execute(select(
        func.count().label("calls"),
        func.count(func.distinct(table.run_id)).label("requests"),
        func.coalesce(func.sum(table.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(table.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(table.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(case((table.source == "unavailable", 1), else_=0)), 0).label("unknown_calls"),
        func.coalesce(func.sum(case((table.source == "estimated", 1), else_=0)), 0).label("estimated_calls"),
        func.avg(table.duration_ms).label("average_duration_ms"),
    ).where(*filters))).mappings().one()
    rows = (await session.scalars(select(table).where(*filters)
        .order_by(table.created_at.desc(), table.id.desc())
        .offset((page - 1) * page_size).limit(page_size))).all()
    items = []
    for row in rows:
        item = {column.name: getattr(row, column.name) for column in table.__table__.columns}
        item["created_at"] = row.created_at.replace(tzinfo=timezone.utc).isoformat()
        items.append(item)
    return {"items": items, "total": summary["calls"], "summary": dict(summary)}
