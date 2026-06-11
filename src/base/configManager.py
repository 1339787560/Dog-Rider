"""YAML 配置管理器

功能：
  - 加载 baseConfig.yaml 作为默认值
  - 支持环境变量覆盖
  - 运行时动态修改
  - 线程安全
"""
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@dataclass
class ModelConfig:
    """模型配置"""
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    max_tokens: int = 4096
    timeout: int = 120
    temperature: float = 0.0


@dataclass
class DiscardConfig:
    """丢弃策略配置"""
    auto_discard: bool = True
    merge_mode: str = "serial"
    merge_on_keep: bool = False
    isFrozenForParallel: bool = True  # 并行模式强制温度为 0


@dataclass
class ContextConfig:
    """上下文三区模型配置"""
    temporary_zone_start: int = 1
    natural_zone_start: int = 10
    cache_granularity: int = 128


@dataclass
class PricingConfig:
    """单价配置 ($/M tokens)"""
    miss: float = 0.14
    hit: float = 0.0028
    output: float = 0.28


@dataclass
class TestConfig:
    """测试相关配置"""
    thread_count: int = 12


@dataclass
class Config:
    """全量配置"""
    model: ModelConfig = field(default_factory=ModelConfig)
    discard: DiscardConfig = field(default_factory=DiscardConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    pricing: Dict[str, PricingConfig] = field(default_factory=dict)
    test: TestConfig = field(default_factory=TestConfig)


class ConfigManager:
    """YAML 配置管理器

    加载逻辑：
    1. 读取 baseConfig.yaml 作为默认值
    2. 环境变量覆盖（DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL）
    3. 运行时动态修改
    """

    def __init__(self, config_path: Optional[str] = None):
        self._lock = threading.Lock()
        if config_path:
            self.config_path = Path(config_path)
        else:
            # 默认位置：项目根目录
            self.config_path = Path(__file__).parent.parent.parent / "baseConfig.yaml"
        self._config = self._load()

    def _load(self) -> Config:
        """加载 YAML 配置并应用环境变量覆盖"""
        # 1. 从 YAML 加载
        raw = {}
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

        # 2. 构建配置对象
        model = ModelConfig(**raw.get("model", {}))
        discard = DiscardConfig(**raw.get("discard", {}))
        context = ContextConfig(**raw.get("context", {}))

        # 3. 单价表
        pricing = {}
        for model_name, prices in raw.get("pricing", {}).items():
            pricing[model_name] = PricingConfig(**prices)

        # 4. 测试配置
        test = TestConfig(**raw.get("test", {}))

        config = Config(
            model=model,
            discard=discard,
            context=context,
            pricing=pricing,
            test=test,
        )

        # 4. 环境变量覆盖
        env_map = {
            "DEEPSEEK_API_KEY": ("model", "api_key"),
            "DEEPSEEK_BASE_URL": ("model", "base_url"),
            "DEEPSEEK_MODEL": ("model", "model"),
            "MERGE_MODE": ("discard", "merge_mode"),
        }

        for env_key, (section, field) in env_map.items():
            if env_key in os.environ:
                obj = getattr(config, section)
                setattr(obj, field, os.environ[env_key])

        # 5. 特殊处理：auto_discard 字符串转 bool
        if "DISCARD_AUTO" in os.environ:
            config.discard.auto_discard = os.environ["DISCARD_AUTO"].lower() in ("1", "true", "yes")

        return config

    def get(self) -> Config:
        """获取当前配置（线程安全读）"""
        with self._lock:
            return self._config

    def set(self, section: str, field: str, value: Any):
        """动态修改配置项

        Args:
            section: "model", "discard", "context"
            field: 字段名
            value: 新值
        """
        with self._lock:
            obj = getattr(self._config, section)
            setattr(obj, field, value)

    def reload(self) -> Config:
        """重新从文件加载配置"""
        with self._lock:
            self._config = self._load()
            return self._config

    def get_pricing(self, model_name: str) -> PricingConfig:
        """获取指定模型的单价配置

        如果未找到，返回 flash 模型的默认值
        """
        with self._lock:
            if model_name in self._config.pricing:
                return self._config.pricing[model_name]
            # fallback
            return PricingConfig(miss=0.14, hit=0.0028, output=0.28)


# 全局单例
_config_manager: Optional[ConfigManager] = None


def load_config(config_path: Optional[str] = None) -> Config:
    """加载配置（便捷函数）

    调用示例：
        from src.base.configManager import load_config
        config = load_config()
        print(config.model.base_url)
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(config_path)
    return _config_manager.get()
