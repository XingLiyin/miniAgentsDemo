"""Skill 路由器。"""


class SkillRouter:
    """基于上下文选择技能。"""

    def select(self, context: dict) -> list:
        """返回技能列表（TODO：匹配策略与加载规则）。"""
        # TODO: 使用 description/标签匹配技能。
        raise NotImplementedError('SkillRouter.select 未实现')
