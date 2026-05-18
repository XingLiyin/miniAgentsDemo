"""Skill runtime object — metadata + source adapter for automatic routing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.skills.definition import SkillDefinition, SkillMetadata
from app.skills.sources import LocalFileSkillSource, SkillSource

if TYPE_CHECKING:
    from app.tools.types import CallContext, ToolResult


@dataclass
class Skill:
    """A skill with an embedded source adapter.

    All content-fetching operations route through ``source`` automatically —
    no external index lookup needed.
    """

    metadata: SkillMetadata
    source: SkillSource

    # ── metadata pass-throughs ────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description

    @property
    def triggers(self) -> list[str]:
        return self.metadata.triggers

    @property
    def version(self) -> str:
        return self.metadata.version

    @property
    def skill_dir(self) -> Path:
        """Directory of the skill; Path('') for remote skills."""
        if isinstance(self.source, LocalFileSkillSource):
            return self.source.skill_dir
        return Path("")

    @property
    def label(self) -> str:
        """Source label: 'local', 'workspace', or 'remote'."""
        return self.source.label

    # ── content operations ────────────────────────────────────────────────────

    def load_definition(self, ctx: "CallContext | None" = None) -> SkillDefinition:
        instructions = self.source.load_instructions(self.name, ctx)
        return SkillDefinition(metadata=self.metadata, instructions=instructions)

    def get_files(self, pattern: str = "**/*", limit: int = 200,
                  ctx: "CallContext | None" = None) -> str:
        return self.source.get_files(self.name, pattern, limit, ctx)

    def load_reference(self, ref_path: str, ctx: "CallContext | None" = None) -> str:
        return self.source.load_reference(self.name, ref_path, ctx)

    def exec_script(self, script_path: str, args: str = "",
                    ctx: "CallContext | None" = None) -> "ToolResult":
        return self.source.exec_script(self.name, script_path, args, ctx)
