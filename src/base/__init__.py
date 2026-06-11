"""Base Agent Framework - Minimal Agent Implementation

可直接基于此框架开发任意 Agent，无需依赖 Dog-Rider 扩展功能。
"""
from .config import BaseConfig, ModelConfig
from .context import BaseContext
from .agent import BaseAgentLoop
from .tools import BaseToolRegistry
from .configManager import ConfigManager, load_config, Config, ModelConfig, DiscardConfig, ContextConfig, PricingConfig

__all__ = [
    "BaseConfig",
    "ModelConfig",
    "BaseContext",
    "BaseAgentLoop",
    "BaseToolRegistry",
    "ConfigManager",
    "load_config",
    "Config",
    "DiscardConfig",
    "ContextConfig",
    "PricingConfig",
]

