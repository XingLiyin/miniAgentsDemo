"""API 路由汇总模块（Phase 1）。"""

from fastapi import APIRouter

from app.api.v1.routes import agent_templates, llms, local_skills, memories, mcp_servers, remote_skill_sources, sessions, tasks, tools, workspace

api_router = APIRouter()
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(memories.router, prefix="/memories", tags=["memories"])
api_router.include_router(tools.router, prefix="/tools", tags=["tools"])
api_router.include_router(llms.router, prefix="/llms", tags=["llms"])
api_router.include_router(agent_templates.router, prefix="/agent-templates", tags=["agent-templates"])
api_router.include_router(mcp_servers.router, prefix="/mcp-servers", tags=["mcp-servers"])
api_router.include_router(remote_skill_sources.router, prefix="/remote-skill-sources", tags=["remote-skill-sources"])
api_router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])
api_router.include_router(local_skills.router, prefix="/skills", tags=["skills"])
