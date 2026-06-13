"""配置模块 - 扩展 BaseConfig 增加丢弃策略和三区上下文配置"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

import yaml

from .base.config import BaseConfig, ModelConfig as BaseModelConfig

PROJECT_ROOT = Path(__file__).parent.parent


@dataclass
class ModelConfig(BaseModelConfig):
    """模型配置 - 继承基础配置"""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"


@dataclass
class PricingConfig:
    """定价配置"""
    miss: float = 0.14
    hit: float = 0.0028
    output: float = 0.28


PRICING: Dict[str, PricingConfig] = {
    "deepseek-v4-flash": PricingConfig(miss=0.14, hit=0.0028, output=0.28),
    "deepseek-v4-pro": PricingConfig(miss=0.435, hit=0.003625, output=0.87),
}


@dataclass
class DiscardConfig:
    """丢弃策略配置"""
    score_threshold_low: float = 0.40
    score_threshold_high: float = 0.65
    w_cache_contrib: float = 0.30
    w_context_ratio: float = 0.20
    w_output_density: float = 0.20
    w_chain_depth: float = 0.15
    w_role_mix: float = 0.15
    cache_hit_rate_good: float = 0.5
    context_ratio_max: float = 0.3
    output_density_good: float = 0.3
    chain_depth_good: int = 3
    high_value_roles: set = field(default_factory=lambda: {"output", "analyze", "write"})
    max_extract_tokens: int = 500
    auto_discard: bool = True  # True=自动pop, False=仅打印判定
    merge_mode: str = "serial"  # "serial"=全量替换, "parallel"=仅追加
    merge_on_keep: bool = False  # KEEP 判定时是否合并到共享上下文
    isFrozenForParallel: bool = True  # 并行模式强制温度为 0


@dataclass
class ContextConfig:
    """上下文三区模型配置"""
    temporary_zone_start: int = 1  # messages[1] 开始是暂时冻结区
    natural_zone_start: int = 10  # messages[10] 开始是自然增长区
    cache_granularity: int = 128


@dataclass
class TestConfig:
    """测试相关配置"""
    thread_count: int = 12


@dataclass
class PersistenceConfig:
    """会话持久化配置"""
    enabled: bool = True
    wal_fsync: bool = True
    checkpoint_interval_sec: int = 60
    checkpoint_request_threshold: int = 20
    cache_dir: str = ".cache/sessions"


@dataclass
class Config(BaseConfig):
    """Dog-Rider 扩展配置 - 增加丢弃策略和三区上下文配置

    继承自 BaseConfig，扩展：
    - discard: 任务级丢弃策略参数
    - context: 三区上下文模型参数
    - test: 测试配置
    - persistence: 会话持久化配置
    """
    discard: DiscardConfig = field(default_factory=DiscardConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    test: TestConfig = field(default_factory=TestConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)


def load_env_config() -> Config:
    """从 baseConfig.yaml + 环境变量加载配置"""
    config = Config()

    # 1. 先从 baseConfig.yaml 加载默认值
    yaml_path = PROJECT_ROOT / "baseConfig.yaml"
    if yaml_path.exists():
        with open(yaml_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        if "model" in raw:
            for k, v in raw["model"].items():
                if hasattr(config.model, k):
                    setattr(config.model, k, v)
        # 顶层标量字段：max_turns / max_retries / max_consecutive_failures / verbose
        for top_key in ("max_turns", "max_retries", "max_consecutive_failures", "verbose"):
            if top_key in raw and hasattr(config, top_key):
                setattr(config, top_key, raw[top_key])
        if "discard" in raw:
            for k, v in raw["discard"].items():
                if hasattr(config.discard, k):
                    setattr(config.discard, k, v)
        if "context" in raw:
            for k, v in raw["context"].items():
                if hasattr(config.context, k):
                    setattr(config.context, k, v)
        if "test" in raw:
            for k, v in raw["test"].items():
                if hasattr(config.test, k):
                    setattr(config.test, k, v)
        if "persistence" in raw:
            for k, v in raw["persistence"].items():
                if hasattr(config.persistence, k):
                    setattr(config.persistence, k, v)

    # 2. 加载 .env
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

    # 3. 环境变量覆盖
    config.model.api_key = os.environ.get("DEEPSEEK_API_KEY", config.model.api_key)
    config.model.base_url = os.environ.get("DEEPSEEK_BASE_URL", config.model.base_url)
    config.model.model = os.environ.get("DEEPSEEK_MODEL", config.model.model)

    if "DISCARD_AUTO" in os.environ:
        config.discard.auto_discard = os.environ["DISCARD_AUTO"].lower() in ("1", "true", "yes")
    if "DISCARD_THRESHOLD" in os.environ:
        config.discard.score_threshold_low = float(os.environ["DISCARD_THRESHOLD"])
    if "MERGE_MODE" in os.environ:
        mode = os.environ["MERGE_MODE"].lower()
        config.discard.merge_mode = mode if mode in ("serial", "parallel") else "serial"

    return config
