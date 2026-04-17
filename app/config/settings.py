"""配置加载模块（Phase 1）。"""

import os
from pathlib import Path
from typing import Any
from pydantic import BaseModel, field_validator

# 直接使用 agent-framework 官方实现：repr 自动屏蔽，str() 返回原值
from agent_framework._settings import SecretString


class Settings(BaseModel):
    """应用配置模型（带默认值，可通过环境变量覆盖）。"""

    model_config = {"arbitrary_types_allowed": True}

    # 应用基本信息
    app_name: str = "miniAgents"
    app_version: str = "0.1.0"

    # 数据目录
    data_dir: Path = Path("data")
    skills_dir: Path = Path("resources/skills")
    agents_dir: Path = Path("resources/agents")

    # LLM 配置
    llm_openai_api_key: SecretString = SecretString("")
    llm_openai_base_url: str = "https://api.openai.com"
    llm_anthropic_api_key: SecretString = SecretString("")
    llm_anthropic_base_url: str = "https://api.anthropic.com"

    @field_validator("llm_openai_api_key", "llm_anthropic_api_key", mode="before")
    @classmethod
    def _to_secret(cls, v: Any) -> "SecretString":
        return v if isinstance(v, SecretString) else SecretString(str(v))
    llm_default_model: str = "gpt-4.1-mini"
    default_llm_timeout_sec: int = 3600

    # Agent 默认配置
    agent_default_system_prompt: str = "You are a helpful agent."
    agent_default_llm_name: str = "openai-main"
    default_agent_template_name: str = "default"
    default_planner_template_name: str = "planner"  # plan sub-agent 使用的默认模板名

    # Agent Loop 默认参数
    default_token_budget: int = 200_000
    default_root_max_turns: int = 20
    default_summary_threshold: int = 20   # 消息条数触发摘要
    default_short_window_size: int = 20   # 上下文消息窗口

    # Lifecycle Manager 并发限制
    max_concurrent_agents: int = 5       # 单 session 最大并发 agent 数
    max_concurrent_tasks: int = 10       # 单 session 最大并发 task 数
    max_spawn_depth: int = 1             # V1 仅支持单层 spawn
    max_retries: int = 1                 # sub-task 最大重试次数

    # Tool 约束
    bash_exec_timeout_ms: int = 30_000
    bash_exec_output_limit_bytes: int = 65_536
    http_request_timeout_ms: int = 10_000
    http_response_limit_bytes: int = 524_288

    # 日志
    log_level: str = "DEBUG"

    # 外部存储（工具 + Skill 语义召回后端，共用同一服务）
    store_base_url: str = ""        # 空 = 禁用，所有同步操作为 no-op
    store_timeout_sec: int = 10


def _load_settings() -> Settings:
    """从 .env 文件和环境变量构建 Settings（前缀 MINIAGENTS_）。

    加载优先级（高 → 低）：系统环境变量 > .env 文件 > 代码默认值。
    """
    from dotenv import load_dotenv
    load_dotenv()  # 读取项目根目录下的 .env，已有系统变量不覆盖

    prefix = "MINIAGENTS_"
    overrides = {}
    for field_name in Settings.model_fields:
        env_key = f"{prefix}{field_name.upper()}"
        val = os.environ.get(env_key)
        if val is not None:
            overrides[field_name] = val
    return Settings(**overrides)


_settings: Settings | None = None


def get_settings() -> Settings:
    """获取配置单例。"""
    global _settings
    if _settings is None:
        _settings = _load_settings()
    return _settings
