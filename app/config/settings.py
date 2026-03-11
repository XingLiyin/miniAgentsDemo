"""配置加载模块。"""

from pydantic import BaseModel


class Settings(BaseModel):
    """应用配置模型。"""

    app_name: str = 'miniAgents'
    app_version: str = '0.1.0'
    log_level: str = 'INFO'

    # LLM 配置
    llm_provider: str = 'openai'
    llm_openai_api_key: str = ''
    llm_openai_base_url: str = 'https://api.openai.com'
    llm_anthropic_api_key: str = ''
    llm_anthropic_base_url: str = 'https://api.anthropic.com'
    llm_default_model: str = 'gpt-4.1-mini'
    llm_timeout_sec: int = 60


_settings = Settings()


def get_settings() -> Settings:
    """获取配置单例。"""
    return _settings
