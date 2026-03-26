"""配置加载模块（Phase 1）。"""

import os
from pathlib import Path
from pydantic import BaseModel


class Settings(BaseModel):
    """应用配置模型（带默认值，可通过环境变量覆盖）。"""

    # 应用基本信息
    app_name: str = "miniAgents"
    app_version: str = "0.1.0"

    # 数据目录
    data_dir: Path = Path("data")
    skills_dir: Path = Path("data/skills")

    # LLM 配置
    llm_openai_api_key: str = ""
    llm_openai_base_url: str = "https://api.openai.com"
    llm_anthropic_api_key: str = ""
    llm_anthropic_base_url: str = "https://api.anthropic.com"
    llm_default_model: str = "gpt-4.1-mini"
    default_llm_timeout_sec: int = 60

    # Agent 默认配置
    agent_default_system_prompt: str = "You are a helpful agent."
    agent_default_llm_name: str = "openai-main"

    # Agent Loop 默认参数
    default_token_budget: int = 200_000
    default_root_max_turns: int = 20
    default_summary_threshold: int = 20   # 消息条数触发摘要
    default_short_window_size: int = 20   # 上下文消息窗口

    # Tool 约束
    bash_exec_timeout_ms: int = 30_000
    bash_exec_output_limit_bytes: int = 65_536
    http_request_timeout_ms: int = 10_000
    http_response_limit_bytes: int = 524_288

    # 日志
    log_level: str = "INFO"


def _load_settings() -> Settings:
    """从环境变量构建 Settings（前缀 MINIAGENTS_）。"""
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
