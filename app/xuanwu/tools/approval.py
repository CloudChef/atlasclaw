"""

Approval mechanism

corresponds to design.md decision 4:Approval mechanism.

for, support ALWAYS_ASK / AUTO_APPROVE / PATTERN_MATCH.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ApprovalPolicy(str, Enum):
    """"""

    ALWAYS_ASK = "always_ask"
    AUTO_APPROVE = "auto_approve"
    PATTERN_MATCH = "pattern_match"


@dataclass
class ApprovalRequest:
    """



    Attributes:
        tool_name:tool name
        command:command string
        reason:
    
"""

    tool_name: str
    command: str
    reason: str = ""


@dataclass
class ApprovalConfig:
    """

singletool configuration

    Attributes:
        policy:
        auto_approve:modelist
        always_ask:modelist
    
"""

    policy: ApprovalPolicy = ApprovalPolicy.PATTERN_MATCH
    auto_approve: list[str] = field(default_factory=list)
    always_ask: list[str] = field(default_factory=list)


class ApprovalManager:
    """

manager

    based onconfigurationcheck user.

    Example usage:
        ```python
        manager = ApprovalManager({
            "exec":ApprovalConfig(
                policy=ApprovalPolicy.PATTERN_MATCH,
                auto_approve=["pip install *", "python *"],
                always_ask=["rm -rf *", "sudo *"],
            ),
        })

        result = manager.check_approval("exec", "rm -rf /tmp")
        # result = ApprovalRequest(tool_name="exec", command="rm -rf /tmp", reason="...")
        ```
    
"""

    def __init__(
        self,
        configs: Optional[dict[str, ApprovalConfig]] = None,
        default_policy: ApprovalPolicy = ApprovalPolicy.PATTERN_MATCH,
    ):
        """


initialize manager

 Args:
 configs:tool configuration
 default_policy:configuration default
 
"""
        self._configs = configs or {}
        self._default_policy = default_policy

    def check_approval(self, tool_name: str, command: str) -> Optional[ApprovalRequest]:
        """

check

        Args:
            tool_name:tool name
            command:command string

        Returns:
            Approval-Request such as, None such as through
        
"""
        config = self._configs.get(tool_name)

        if config is None:
            # configuration:default
            if self._default_policy == ApprovalPolicy.ALWAYS_ASK:
                return ApprovalRequest(
                    tool_name=tool_name,
                    command=command,
                    reason="default policy: always_ask",
                )
            # default through
            return None

        if config.policy == ApprovalPolicy.AUTO_APPROVE:
            return None

        if config.policy == ApprovalPolicy.ALWAYS_ASK:
            return ApprovalRequest(
                tool_name=tool_name,
                command=command,
                reason="policy: always_ask",
            )

        # PATTERN_MATCH
        return self._pattern_match(tool_name, command, config)

    def _pattern_match(
        self, tool_name: str, command: str, config: ApprovalConfig
    ) -> Optional[ApprovalRequest]:
        """

mode check

        :always_ask > auto_approve > defaultthrough
        
"""
        # check always_ask
        for pattern in config.always_ask:
            if fnmatch.fnmatch(command, pattern):
                return ApprovalRequest(
                    tool_name=tool_name,
                    command=command,
                    reason=f"matches always_ask pattern: {pattern}",
                )

        # check auto_approve
        for pattern in config.auto_approve:
            if fnmatch.fnmatch(command, pattern):
                return None  # through

        # :defaultthrough(PATTERN_MATCH)
        return None
