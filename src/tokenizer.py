"""分词器模块"""
import json
from pathlib import Path
from typing import List, Tuple
from tokenizers import Tokenizer as HFTokenizer
from .config import PROJECT_ROOT

CACHE_DIR = PROJECT_ROOT / "Poc" / ".cache"
TOKENIZER_PATH = CACHE_DIR / "tokenizer.json"


class DeepSeekTokenizer:
    """DeepSeek V3/V4 BPE 分词器封装"""

    CACHE_GRANULARITY = 128
    VOCAB_SIZE = 128815

    def __init__(self, tokenizer_path: Path = None):
        path = tokenizer_path or TOKENIZER_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"tokenizer.json not found at {path}\n"
                f"Download: https://huggingface.co/deepseek-ai/DeepSeek-V3/resolve/main/tokenizer.json"
            )
        self._tok = HFTokenizer.from_file(str(path))

    def count(self, text: str) -> int:
        """返回文本的 token 数量"""
        return len(self._tok.encode(text).ids)

    def encode(self, text: str) -> List[int]:
        """返回 token ID 列表"""
        return self._tok.encode(text).ids

    def tokens(self, text: str) -> List[str]:
        """返回 token 字符串列表"""
        return self._tok.encode(text).tokens

    def common_prefix_len(self, text_a: str, text_b: str) -> int:
        """计算两段文本的 token 级别公共前缀长度"""
        ids_a, ids_b = self.encode(text_a), self.encode(text_b)
        n = 0
        for a, b in zip(ids_a, ids_b):
            if a == b:
                n += 1
            else:
                break
        return n

    def cache_hit_tokens(self, prefix_len: int) -> int:
        """给定前缀 token 数，返回实际命中的缓存 token 数 (128 对齐)"""
        if prefix_len < self.CACHE_GRANULARITY:
            return 0
        return (prefix_len // self.CACHE_GRANULARITY) * self.CACHE_GRANULARITY

    def estimate_cache_hit(self, stable_prefix: str, full_input: str) -> Tuple[int, int]:
        """估算缓存命中情况"""
        common = self.common_prefix_len(stable_prefix, full_input)
        hit = self.cache_hit_tokens(common)
        total = self.count(full_input)
        return hit, total - hit


class TemplateEstimator:
    """Chat template token 开销模型

    实测公式 (deepseek-v4-flash):
        overhead = 4                       (n_messages ≤ 2)
        overhead = 4 + 2 × (n_messages - 2)  (n_messages > 2)
    """

    BASE_OVERHEAD = 4
    PER_MESSAGE_OVERHEAD = 2

    @staticmethod
    def estimate(n_messages: int, content_tokens: int) -> int:
        """根据消息数量和内容 token 数估算 API prompt_tokens"""
        overhead = TemplateEstimator.BASE_OVERHEAD
        if n_messages > 2:
            overhead += TemplateEstimator.PER_MESSAGE_OVERHEAD * (n_messages - 2)
        return content_tokens + overhead

    @staticmethod
    def content_tokens_from_api(api_prompt_tokens: int, n_messages: int) -> int:
        """从 API 返回的 prompt_tokens 反推内容 token 数"""
        overhead = TemplateEstimator.BASE_OVERHEAD
        if n_messages > 2:
            overhead += TemplateEstimator.PER_MESSAGE_OVERHEAD * (n_messages - 2)
        return api_prompt_tokens - overhead


# 全局单例
_global_tokenizer: DeepSeekTokenizer = None


def get_tokenizer() -> DeepSeekTokenizer:
    """获取分词器单例"""
    global _global_tokenizer
    if _global_tokenizer is None:
        _global_tokenizer = DeepSeekTokenizer()
    return _global_tokenizer
