"""SkillRouter：识别 skill task，加载 Level 2/3，封装执行上下文。"""

from __future__ import annotations

import logging

from app.skills.definition import SkillDefinition
from app.skills.loader import SkillLoader
from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class SkillRouter:
    """运行时 Skill 调度器。

    - load_for_task()      — 触发 Level 2 加载，返回 SkillDefinition
    - build_skill_prompt() — 拼合 skill task 的 system prompt
    - load_resource()      — Level 3 按需加载资源文件
    """

    def __init__(self, skill_registry: SkillRegistry) -> None:
        self._registry = skill_registry
        self._loader = SkillLoader()

    def load_for_task(self, skill_name: str) -> SkillDefinition | None:
        """加载指定 skill 的 Level 2 内容（Instructions）。"""
        return self._registry.load_definition(skill_name)

    def build_skill_prompt(self, skill_def: SkillDefinition, task_inputs: dict) -> str:
        """拼合 skill task 执行时使用的 system prompt。

        格式：
          [可选] Context: {task_inputs["context"]}

          {skill_def.instructions}
        """
        parts: list[str] = []
        context = task_inputs.get("context", "").strip()
        if context:
            parts.append(f"Context:\n{context}")
        parts.append(skill_def.instructions)
        return "\n\n".join(parts)

    def load_resource(self, skill_name: str, resource_path: str) -> str | None:
        """Level 3：按需加载 skill 目录内的资源文件。

        返回文件内容字符串；skill 不存在或读取失败时返回 None。
        """
        meta = self._registry.get_metadata(skill_name)
        if meta is None:
            logger.warning("SkillRouter.load_resource: skill '%s' not found", skill_name)
            return None
        try:
            return self._loader.load_resource(meta.skill_dir, resource_path)
        except Exception as e:
            logger.warning(
                "SkillRouter.load_resource: failed to load '%s/%s': %s",
                skill_name, resource_path, e,
            )
            return None
