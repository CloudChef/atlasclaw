# -*- coding: utf-8 -*-
"""DingTalk approval todo handler.

This module provides the handler for querying user's pending approval tasks.
Supports query by userid or mobile number.
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
    "name": "approval-todo",
    "description": "查询用户的待审批任务数量",
    "category": "provider:dingtalk",
    "provider_type": "dingtalk",
    "instance_required": True,
}


async def handler(
    ctx: "RunContext[SkillDeps]",
    userid: Optional[str] = None,
    mobile: Optional[str] = None,
) -> Dict[str, Any]:
    """Query user's pending approval task count.

    Supports two query modes:
    1. Direct query by userid
    2. Query by mobile (first finds userid, then queries todo count)

    Args:
        ctx: RunContext with SkillDeps containing provider configuration.
        userid: User's userId (direct query).
        mobile: User's mobile number (will lookup userid first).

    Returns:
        Standard response dict with success, message, and data fields.
    """
    # Validate parameters
    if not userid and not mobile:
        return {
            "success": False,
            "message": "参数错误: 必须提供 userid 或 mobile",
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
            # Determine userid
            target_userid = userid

            # If no userid provided, lookup by mobile
            if not target_userid and mobile:
                user_info = await client.get_user_by_mobile(mobile)
                target_userid = user_info.get("userid")
                if not target_userid:
                    return {
                        "success": False,
                        "message": f"未找到手机号 {mobile} 对应的用户",
                        "data": {},
                    }

            # Query todo count
            if not target_userid:
                return {
                    "success": False,
                    "message": "无法确定用户ID",
                    "data": {},
                }

            todo_result = await client.get_todo_num(target_userid)
            count = todo_result.get("count", 0)

            return {
                "success": True,
                "message": f"查询成功，用户 {target_userid} 有 {count} 条待审批任务",
                "data": {
                    "userid": target_userid,
                    "count": count,
                },
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
        logger.exception("Unexpected error querying todo count")
        return {
            "success": False,
            "message": f"查询待办任务失败: {str(e)}",
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
        userid = os.environ.get("USERID", "")
        mobile = os.environ.get("MOBILE", "")

        # Validate parameters
        if not userid and not mobile:
            result = {
                "success": False,
                "message": "参数错误: 必须提供 USERID 或 MOBILE 环境变量",
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
                # Determine userid
                target_userid = userid

                # If no userid provided, lookup by mobile
                if not target_userid and mobile:
                    user_info = await client.get_user_by_mobile(mobile)
                    target_userid = user_info.get("userid")
                    if not target_userid:
                        result = {
                            "success": False,
                            "message": f"未找到手机号 {mobile} 对应的用户",
                            "data": {},
                        }
                        print(json.dumps(result, ensure_ascii=False))
                        return

                # Query todo count
                if not target_userid:
                    result = {
                        "success": False,
                        "message": "无法确定用户ID",
                        "data": {},
                    }
                    print(json.dumps(result, ensure_ascii=False))
                    return

                todo_result = await client.get_todo_num(target_userid)
                count = todo_result.get("count", 0)

                result = {
                    "success": True,
                    "message": f"查询成功，用户 {target_userid} 有 {count} 条待审批任务",
                    "data": {"userid": target_userid, "count": count},
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
                "message": f"查询待办任务失败: {str(e)}",
                "data": {},
            }

        print(json.dumps(result, ensure_ascii=False))

    asyncio.run(main())
