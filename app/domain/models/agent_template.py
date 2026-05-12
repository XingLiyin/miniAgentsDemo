"""AgentTemplate domain model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentTemplate:
    """Static agent template used to initialize runtime agents."""

    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""

    # "global" = built-in from resources/agents/; "workspace" = user-defined from {workspace_dir}/.agents/
    scope: str = "global"
    source_dir: str = "" # {$AGENT_DIR}/agent-name for global templates, {$WORKSPACE_DIR}/.agents/agent-name for workspace templates
    workspace_dir: str = ""  # non-empty only when scope == "workspace"

    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "scope": self.scope,
            "source_dir": self.source_dir,
            "workspace_dir": self.workspace_dir,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentTemplate":
        return cls(
            id=d["id"],
            name=d["name"],
            version=d.get("version", "1.0.0"),
            description=d.get("description", ""),
            scope=d.get("scope", "global"),
            source_dir=d.get("source_dir", ""),
            workspace_dir=d.get("workspace_dir", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
