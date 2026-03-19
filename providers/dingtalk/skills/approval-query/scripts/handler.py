# -*- coding: utf-8 -*-
"""DingTalk approval query handler.

This module provides the handler for querying DingTalk approval instances.
Supports both single instance detail query and instance list query.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps

# Add _shared directory to path for importing shared client
_shared_dir = Path(__file__).parent.parent.parent / "_shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))

from dingtalk_client import DingTalkApprovalClient, DingTalkAPIError

logger = logging.getLogger(__name__)


SKILL_METADATA = {
    "name": "approval-query",
    "description": "查询钉钉审批实例状态和详情",
    "category": "provider:dingtalk",
    "provider_type": "dingtalk",
    "instance_required": True,
}


async def handler(
    ctx: "RunContext[SkillDeps]",
    process_instance_id: Optional[str] = None,
    process_code: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Query DingTalk approval instance(s).

    Supports two query modes:
    1. Single instance query: Provide process_instance_id to get instance details
    2. List query: Provide process_code to get instance ID list

    Args:
        ctx: RunContext with SkillDeps containing provider configuration.
        process_instance_id: Approval instance ID (for single instance query).
        process_code: Approval template code (for list query).
        start_time: Start timestamp in milliseconds (for list query).
        end_time: End timestamp in milliseconds (for list query).

    Returns:
        Standard response dict with success, message, and data fields.
    """
    # Validate parameters
    if not process_instance_id and not process_code:
        return {
            "success": False,
            "message": "参数错误: 必须提供 process_instance_id 或 process_code",
            "data": {},
        }

    try:
        # Extract provider configuration from context
        provider_config = _get_provider_config(ctx)

        app_key = provider_config.get("app_key", "")
        app_secret = provider_config.get("app_secret", "")
        agent_id = provider_config.get("agent_id", "")
        base_url = provider_config.get("base_url", "https://oapi.dingtalk.com")

        # Create client instance
        async with DingTalkApprovalClient(
            app_key=app_key,
            app_secret=app_secret,
            agent_id=agent_id,
            base_url=base_url,
        ) as client:
            # Single instance query mode
            if process_instance_id:
                instance_detail = await client.get_process_instance(process_instance_id)
                return {
                    "success": True,
                    "message": "查询成功",
                    "data": instance_detail,
                }

            # List query mode
            if process_code:
                # Parse time parameters
                start_ts = int(start_time) if start_time else None
                end_ts = int(end_time) if end_time else None

                result = await client.list_process_instance_ids(
                    process_code=process_code,
                    start_time=start_ts,
                    end_time=end_ts,
                )

                instance_ids = result.get("list", [])
                next_cursor = result.get("next_cursor")

                response_data: Dict[str, Any] = {
                    "instance_ids": instance_ids,
                }
                if next_cursor is not None:
                    response_data["next_cursor"] = next_cursor

                return {
                    "success": True,
                    "message": f"查询成功，共 {len(instance_ids)} 条记录",
                    "data": response_data,
                }

            return {
                "success": False,
                "message": "参数错误: 未指定查询条件",
                "data": {},
            }

    except DingTalkAPIError as e:
        logger.error("DingTalk API error: %s", e)
        return {
            "success": False,
            "message": f"钉钉API错误: {e.errmsg}",
            "data": {
                "error_code": e.errcode,
                "error_message": e.errmsg,
            },
        }

    except ValueError as e:
        logger.error("Configuration error: %s", e)
        return {
            "success": False,
            "message": f"配置错误: {str(e)}",
            "data": {},
        }

    except Exception as e:
        logger.exception("Unexpected error querying approval instance")
        return {
            "success": False,
            "message": f"查询审批实例失败: {str(e)}",
            "data": {},
        }


def _get_provider_config(ctx: "RunContext[SkillDeps]") -> Dict[str, Any]:
    """Extract provider configuration from context.

    Args:
        ctx: RunContext with SkillDeps.

    Returns:
        Provider configuration dictionary.
    """
    if not hasattr(ctx, "deps") or not hasattr(ctx.deps, "extra"):
        return {}

    extra = ctx.deps.extra
    if not isinstance(extra, dict):
        return {}

    # Check for directly selected provider instance
    provider_instance = extra.get("provider_instance")
    if isinstance(provider_instance, dict):
        return provider_instance

    # Fall back to provider_instances lookup
    provider_instances = extra.get("provider_instances", {})
    dingtalk_instances = provider_instances.get("dingtalk", {})
    if dingtalk_instances:
        # Return first available instance
        return next(iter(dingtalk_instances.values()), {})

    return {}


if __name__ == "__main__":
    import asyncio
    import json
    import os

    async def main() -> None:
        """CLI entry point for script execution mode."""
        # Read provider config from environment variables
        app_key = os.environ.get("APP_KEY", "")
        app_secret = os.environ.get("APP_SECRET", "")
        agent_id = os.environ.get("AGENT_ID", "")
        base_url = os.environ.get("BASE_URL", "https://oapi.dingtalk.com")

        # Read business parameters from environment variables
        process_instance_id = os.environ.get("PROCESS_INSTANCE_ID", "")
        process_code = os.environ.get("PROCESS_CODE", "")
        start_time = os.environ.get("START_TIME", "")
        end_time = os.environ.get("END_TIME", "")

        # Validate parameters
        if not process_instance_id and not process_code:
            result = {
                "success": False,
                "message": "参数错误: 必须提供 PROCESS_INSTANCE_ID 或 PROCESS_CODE 环境变量",
                "data": {},
            }
            print(json.dumps(result, ensure_ascii=False))
            return

        try:
            async with DingTalkApprovalClient(
                app_key=app_key,
                app_secret=app_secret,
                agent_id=agent_id,
                base_url=base_url,
            ) as client:
                # Single instance query mode
                if process_instance_id:
                    instance_detail = await client.get_process_instance(process_instance_id)
                    result = {
                        "success": True,
                        "message": "查询成功",
                        "data": instance_detail,
                    }
                # List query mode
                elif process_code:
                    start_ts = int(start_time) if start_time else None
                    end_ts = int(end_time) if end_time else None

                    list_result = await client.list_process_instance_ids(
                        process_code=process_code,
                        start_time=start_ts,
                        end_time=end_ts,
                    )

                    instance_ids = list_result.get("list", [])
                    next_cursor = list_result.get("next_cursor")

                    response_data = {"instance_ids": instance_ids}
                    if next_cursor is not None:
                        response_data["next_cursor"] = next_cursor

                    result = {
                        "success": True,
                        "message": f"查询成功，共 {len(instance_ids)} 条记录",
                        "data": response_data,
                    }
                else:
                    result = {
                        "success": False,
                        "message": "参数错误: 未指定查询条件",
                        "data": {},
                    }

        except DingTalkAPIError as e:
            result = {
                "success": False,
                "message": f"钉钉API错误: {e.errmsg}",
                "data": {"error_code": e.errcode, "error_message": e.errmsg},
            }
        except Exception as e:
            result = {
                "success": False,
                "message": f"查询审批实例失败: {str(e)}",
                "data": {},
            }

        print(json.dumps(result, ensure_ascii=False))

    asyncio.run(main())
