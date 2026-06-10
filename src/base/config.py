"""基础配置 - 无 Dog-Rider 扩展功能"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass
class ModelConfig:
    """模型配置"""
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    max_tokens: int = 4096
    timeout: int = 120


@dataclass
class BaseConfig:
    """基础 Agent 配置 - 仅包含最小功能集"""
    model: ModelConfig = field(default_factory=ModelConfig)
    verbose: bool = False
    max_turns: int = 50        # 单任务最大对话轮次
    max_retries: int = 3       # API 调用失败最大重试次数

    @classmethod
    def from_env(cls) -> "BaseConfig":
        """从环境变量加载配置"""
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        return cls(
            model=ModelConfig(
                api_key=api_key,
                base_url=base_url,
                model=model,
            ),
            verbose=os.getenv("VERBOSE", "0") == "1",
        )
