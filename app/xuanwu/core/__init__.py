"""



Includes:
- deps:SkillDeps dependency-injection container(PydanticAI RunContext[T] typeparameter)
- config:Configuration manager
- config_schema:configuration Schema(Pydantic model)
"""

from app.xuanwu.core.deps import SkillDeps

__all__ = ["SkillDeps"]
