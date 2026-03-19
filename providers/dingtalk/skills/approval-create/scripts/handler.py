# -*- coding: utf-8 -*-
"""DingTalk approval creation handler.

This module provides the handler for creating DingTalk approval instances.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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
    "name": "approval-create",
    "description": "发起钉钉审批实例（报销、请假、采购等）",
    "category": "provider:dingtalk",
    "provider_type": "dingtalk",
    "instance_required": True,
}


async def handler(
    ctx: "RunContext[SkillDeps]",
    process_code: str,
    originator_user_id: str,
    dept_id: int,
    form_component_values: List[Dict[str, str]],
    approvers: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a DingTalk approval instance.

    Args:
        ctx: RunContext with SkillDeps containing provider configuration.
        process_code: Approval template unique identifier.
        originator_user_id: Initiator's userId.
        dept_id: Initiator's department ID.
        form_component_values: Form parameters, format: [{"name": "Field", "value": "Value"}].
        approvers: Optional approver userIds, comma-separated.

    Returns:
        Standard response dict with success, message, and data fields.
    """
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
            # Parse approvers if provided
            approvers_list = None
            if approvers:
                approvers_list = [a.strip() for a in approvers.split(",") if a.strip()]

            # Create approval instance
            process_instance_id = await client.create_process_instance(
                process_code=process_code,
                originator_user_id=originator_user_id,
                dept_id=dept_id,
                form_values=form_component_values,
                approvers=approvers_list,
            )

            return {
                "success": True,
                "message": "审批实例创建成功",
                "data": {
                    "process_instance_id": process_instance_id,
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
        logger.exception("Unexpected error creating approval instance")
        return {
            "success": False,
            "message": f"创建审批实例失败: {str(e)}",
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
        process_code = os.environ.get("PROCESS_CODE", "")
        originator_user_id = os.environ.get("ORIGINATOR_USER_ID", "")
        dept_id_str = os.environ.get("DEPT_ID", "0")
        form_values_json = os.environ.get("FORM_COMPONENT_VALUES", "[]")
        approvers = os.environ.get("APPROVERS", "")

        # Validate required parameters
        if not process_code or not originator_user_id:
            result = {
                "success": False,
                "message": "参数错误: 必须提供 PROCESS_CODE 和 ORIGINATOR_USER_ID 环境变量",
                "data": {},
            }
            print(json.dumps(result, ensure_ascii=False))
            return

        try:
            dept_id = int(dept_id_str)
            form_component_values = json.loads(form_values_json)
        except (ValueError, json.JSONDecodeError) as e:
            result = {
                "success": False,
                "message": f"参数解析错误: {str(e)}",
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
                approvers_list = None
                if approvers:
                    approvers_list = [a.strip() for a in approvers.split(",") if a.strip()]

                process_instance_id = await client.create_process_instance(
                    process_code=process_code,
                    originator_user_id=originator_user_id,
                    dept_id=dept_id,
                    form_values=form_component_values,
                    approvers=approvers_list,
                )

                result = {
                    "success": True,
                    "message": "审批实例创建成功",
                    "data": {"process_instance_id": process_instance_id},
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
                "message": f"创建审批实例失败: {str(e)}",
                "data": {},
            }

        print(json.dumps(result, ensure_ascii=False))

    asyncio.run(main())
