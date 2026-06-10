"""上下文管理 - 三区模型"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import PRICING, Config, ContextConfig
from .tokenizer import DeepSeekTokenizer, get_tokenizer


@dataclass
class ZoneStats:
    """区域统计"""
    name: str
    tokens: int = 0
    message_count: int = 0


@dataclass
class ContextAnalysis:
    """上下文分析结果"""
    permanent: ZoneStats
    temporary: ZoneStats
    natural: ZoneStats
    total_tokens: int
    cache_hit_estimate: int
    cache_miss_estimate: int
    hit_rate: float
    cost_estimate_all_miss: float
    cost_estimate_with_cache: float
    suggestions: List[str] = field(default_factory=list)


class ContextManager:
    """三区上下文管理器

    模型:
        messages[0]                          = 永久冻结区 (system prompt)
        messages[1 : natural_zone_start]     = 暂时冻结区 (已确认内容)
        messages[natural_zone_start :]       = 自然增长区 (当前任务)
    """

    def __init__(self, config: Config, tokenizer: DeepSeekTokenizer = None):
        self.config = config
        self.tok = tokenizer or get_tokenizer()
        self.messages: List[dict] = []

    def init_with_system(self, system_prompt: str):
        """初始化上下文 (设置永久冻结区)"""
        self.messages = [{"role": "system", "content": system_prompt}]

    def append(self, message: dict):
        """追加消息到自然增长区"""
        self.messages.append(message)

    def extend(self, messages: List[dict]):
        """批量追加消息"""
        self.messages.extend(messages)

    def snapshot(self) -> "ContextManager":
        """创建独立副本用于隔离任务执行。

        深拷贝 messages 列表，共享 config 和 tokenizer 引用。
        副本的修改不影响原始上下文。
        """
        clone = ContextManager(self.config, self.tok)
        clone.messages = [dict(m) for m in self.messages]
        return clone

    def replace_with(self, other: "ContextManager"):
        """用另一个上下文的副本替换当前消息列表 (KEEP 判定)。"""
        self.messages = [dict(m) for m in other.messages]

    def merge_extracted(self, working: "ContextManager",
                        extracted_messages: List[dict], summary: str = ""):
        """从工作副本中合并提取的高价值消息 (PARTIAL 判定)。"""
        if summary:
            self.messages.append({"role": "system", "content": f"[PARTIAL KEEP] {summary}"})
        for m in extracted_messages:
            self.messages.append(dict(m))

    def pop_natural(self, n: int) -> List[dict]:
        """[DEPRECATED] 从自然增长区末尾弹出 n 条消息。

        已被 snapshot + merge 模式替代，保留用于向后兼容。
        """
        start = self.config.context.natural_zone_start
        if len(self.messages) <= start:
            return []
        actual_n = min(n, len(self.messages) - start)
        popped = self.messages[-actual_n:]
        self.messages = self.messages[:-actual_n]
        return popped

    def get_zone_messages(self, zone_key: str) -> List[dict]:
        """获取指定区域的消息"""
        if zone_key == "permanent":
            return self.messages[:1] if self.messages else []
        elif zone_key == "temporary":
            start = 1
            end = self.config.context.natural_zone_start
            return self.messages[start:end]
        elif zone_key == "natural":
            start = self.config.context.natural_zone_start
            return self.messages[start:]
        return []

    def build_prefix(self) -> str:
        """构建 shared prefix (永久 + 暂时)"""
        prefix = []
        for m in self.get_zone_messages("permanent"):
            prefix.append(m.get("content", ""))
        for m in self.get_zone_messages("temporary"):
            prefix.append(m.get("content", ""))
        return "\n".join(prefix)

    def analyze(self) -> ContextAnalysis:
        """分析当前上下文状态"""
        cfg = self.config.context

        # 各区域统计
        permanent_msgs = self.get_zone_messages("permanent")
        temporary_msgs = self.get_zone_messages("temporary")
        natural_msgs = self.get_zone_messages("natural")

        permanent_tokens = sum(self.tok.count(m.get("content", "")) for m in permanent_msgs)
        temporary_tokens = sum(self.tok.count(m.get("content", "")) for m in temporary_msgs)
        natural_tokens = sum(self.tok.count(m.get("content", "")) for m in natural_msgs)

        total = permanent_tokens + temporary_tokens + natural_tokens
        prefix_tokens = permanent_tokens + temporary_tokens

        # 缓存估算
        cache_hit = self.tok.cache_hit_tokens(prefix_tokens)
        cache_miss = total - cache_hit
        hit_rate = cache_hit / total * 100 if total > 0 else 0

        # 费用估算
        price = PRICING.get(self.config.model.model, PRICING["deepseek-v4-flash"])
        cost_all_miss = total * price.miss / 1_000_000
        cost_with_cache = (cache_miss * price.miss + cache_hit * price.hit) / 1_000_000

        # 优化建议
        suggestions = []
        if natural_tokens > permanent_tokens + temporary_tokens:
            suggestions.append("自然增长区 > 冻结区之和 → 考虑压缩或回滚")
        if permanent_tokens < cfg.cache_granularity:
            suggestions.append(f"永久冻结区 < {cfg.cache_granularity} token → 不触发缓存")
        if natural_tokens > 500:
            suggestions.append("自然增长区 token 较多 → 及时压缩末尾低价值操作")
        if hit_rate < 50 and total > 0:
            suggestions.append("缓存命中率 < 50% → 检查前缀稳定性")

        return ContextAnalysis(
            permanent=ZoneStats(name="永久冻结区", tokens=permanent_tokens, message_count=len(permanent_msgs)),
            temporary=ZoneStats(name="暂时冻结区", tokens=temporary_tokens, message_count=len(temporary_msgs)),
            natural=ZoneStats(name="自然增长区", tokens=natural_tokens, message_count=len(natural_msgs)),
            total_tokens=total,
            cache_hit_estimate=cache_hit,
            cache_miss_estimate=cache_miss,
            hit_rate=hit_rate,
            cost_estimate_all_miss=cost_all_miss,
            cost_estimate_with_cache=cost_with_cache,
            suggestions=suggestions,
        )

    def report(self, analysis: ContextAnalysis = None) -> str:
        """生成分析报告"""
        a = analysis or self.analyze()
        price = PRICING.get(self.config.model.model, PRICING["deepseek-v4-flash"])

        lines = [
            "=" * 60,
            "三区上下文报告",
            "=" * 60,
            f"  {a.permanent.name:12s}: {a.permanent.tokens:6d} tokens ({a.permanent.message_count} msg)",
            f"  {a.temporary.name:12s}: {a.temporary.tokens:6d} tokens ({a.temporary.message_count} msg)",
            f"  {a.natural.name:12s}: {a.natural.tokens:6d} tokens ({a.natural.message_count} msg)",
            f"  {'总计':12s}: {a.total_tokens:6d} tokens",
            "",
            f"  Cache 估算: hit={a.cache_hit_estimate} miss={a.cache_miss_estimate} ({a.hit_rate:.1f}%)",
            f"  费用估算: 全 miss=${a.cost_estimate_all_miss:.6f}, 有缓存=${a.cost_estimate_with_cache:.6f}",
            f"  节省: {(1 - a.cost_estimate_with_cache / a.cost_estimate_all_miss) * 100:.1f}%" if a.cost_estimate_all_miss > 0 else "  节省: N/A",
            "",
        ]

        if a.suggestions:
            lines.append("  优化建议:")
            for s in a.suggestions:
                lines.append(f"    ⚠ {s}")
        else:
            lines.append("  ✓ 上下文状态良好")

        lines.append("=" * 60)
        return "\n".join(lines)

    def get_natural_task_boundary(self) -> int:
        """获取当前任务在自然增长区中的消息数量边界

        返回从自然增长区开始到当前末尾的消息数
        """
        start = self.config.context.natural_zone_start
        return max(0, len(self.messages) - start)
