"""配置加载模块。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def resolve_working_dir(raw: str) -> str:
    """Resolve working_dir to an absolute path.

    Absolute paths pass through unchanged.
    Relative paths are joined with workspace_base_dir (if set) then resolved to absolute,
    ensuring the result is always absolute and this function is idempotent.
    """
    if not raw:
        return ""
    p = Path(raw)
    if p.is_absolute():
        return raw
    base = get_settings().workspace_base_dir
    if base:
        return str((Path(base) / p).resolve())
    return str(p.resolve())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IPMASTER_COWORK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用基本信息
    app_name: str = "IPMaster-Cowork"
    app_version: str = "0.1.0"

    # 数据目录
    data_dir: Path = Path("data")
    skills_dir: Path = Path("resources/skills")
    agents_dir: Path = Path("resources/agents")
    workspace_base_dir: str = ""  # 相对 working_dir 的解析根目录，Docker 下设为挂载点如 /workspace

    # LLM 默认配置 — provider 名称
    default_llm_provider: str = ""
    default_llm_model: str = ""
    default_llm_timeout_sec: int = 3600
    # LLM 完整客户端配置 — 填写后无需提前在数据库注册 provider
    default_llm_style: str = "openai"          # openai 或 anthropic
    default_llm_api_key: str = ""
    default_llm_base_url: str = ""
    default_llm_context_limit: int = 200_000
    default_llm_max_output_tokens: int = 8192
    # TLS — 内网中间人代理场景：用操作系统证书库（含公司根 CA）替代 certifi
    use_system_truststore: bool = True

    # Agent 默认配置
    default_agent_template_name: str = "default"
    default_planner_template_name: str = "planner"

    # Agent Loop 默认参数
    default_token_budget: int = 0
    default_actor_max_tool_rounds: int = 50
    default_summary_threshold: int = 20
    default_context_limit: int = 200_000
    default_short_window_size: int = 20
    compaction_keep_last: int = 6

    # 资源描述总结（长 tool/skill/agent 描述压缩，缓解上下文膨胀）
    resource_summary_enabled: bool = True
    resource_summary_threshold_tokens: int = 200   # 超过此 token 数才总结
    resource_summary_target_tokens: int = 60        # 总结目标长度
    resource_summary_wait_timeout_sec: float = 30.0  # 读路径阻塞等待上限
    resource_summary_cooldown_sec: float = 60.0      # 总结失败后的冷却时间

    # Lifecycle Manager 并发限制
    max_concurrent_agents: int = 5
    max_concurrent_tasks: int = 10
    max_spawn_depth: int = 2
    max_task_retries: int = 3

    # Tool 约束
    bash_exec_cwd: str = ""
    bash_exec_timeout_ms: int = 30_000
    bash_exec_output_limit_bytes: int = 65_536
    http_request_timeout_ms: int = 10_000
    http_response_limit_bytes: int = 524_288
    enable_builtin_tools: bool = True  # 是否注册内置工具（skill_executor 和 control tools 始终注册）

    # 远端 Skill 拉取服务器
    skill_pull_server_url: str = "http://10.25.228.203:8080/api"  # 远端 skill 服务器 base URL，如 https://example.com/api

    # 外部存储
    store_base_url: str = ""
    store_timeout_sec: int = 10

    # 日志
    log_level: str = "DEBUG"
    log_dir: str = ""  # 日志文件目录，为空则不写文件；每天生成新文件


@lru_cache
def get_settings() -> Settings:
    return Settings()
