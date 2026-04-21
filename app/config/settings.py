"""配置加载模块。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MINIAGENTS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用基本信息
    app_name: str = "miniAgents"
    app_version: str = "0.1.0"

    # 数据目录
    data_dir: Path = Path("data")
    skills_dir: Path = Path("resources/skills")
    agents_dir: Path = Path("resources/agents")

    # LLM 配置
    default_llm_model: str = "Qwen/Qwen3.5-27B"
    default_llm_provider: str = "ms-openai"
    default_llm_timeout_sec: int = 3600

    # Agent 默认配置
    default_agent_template_name: str = "default"
    default_planner_template_name: str = "planner"

    # Agent Loop 默认参数
    default_token_budget: int = 200_000
    default_root_max_turns: int = 20
    default_summary_threshold: int = 20
    default_short_window_size: int = 20

    # Lifecycle Manager 并发限制
    max_concurrent_agents: int = 5
    max_concurrent_tasks: int = 10
    max_spawn_depth: int = 1
    max_retries: int = 1

    # Tool 约束
    bash_exec_cwd: str = ""
    bash_exec_timeout_ms: int = 30_000
    bash_exec_output_limit_bytes: int = 65_536
    http_request_timeout_ms: int = 10_000
    http_response_limit_bytes: int = 524_288

    # 外部存储
    store_base_url: str = ""
    store_timeout_sec: int = 10

    # 日志
    log_level: str = "INFO"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
