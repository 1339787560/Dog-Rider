"""任务级丢弃策略模块"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .config import Config, DiscardConfig
from .tokenizer import DeepSeekTokenizer, get_tokenizer


@dataclass
class SubRequest:
    """任务中的单次请求记录"""

    ROLE_EXPLORATION = "exploration"
    ROLE_READ = "read"
    ROLE_ANALYZE = "analyze"
    ROLE_OUTPUT = "output"
    ROLE_WRITE = "write"

    role: str
    tokens: int = 0
    cache_hit: int = 0
    cache_miss: int = 0
    output_tokens: int = 0
    label: str = ""

    @property
    def miss_rate(self) -> float:
        return self.cache_miss / self.tokens if self.tokens > 0 else 0

    @property
    def is_high_hit(self) -> bool:
        """是否高缓存命中"""
        return self.cache_hit > 0 and self.miss_rate < 0.5

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "tokens": self.tokens,
            "cache_hit": self.cache_hit,
            "cache_miss": self.cache_miss,
            "output_tokens": self.output_tokens,
            "miss_rate": self.miss_rate,
            "label": self.label,
        }


@dataclass
class TaskVerdict:
    """任务价值判定结果"""

    is_low_value: bool
    reason: str
    score: float
    high_value_requests: List[SubRequest] = field(default_factory=list)
    summary: str = ""


class ValueJudge:
    """单次操作低价值判定器"""

    def __init__(self, config: DiscardConfig, tokenizer: DeepSeekTokenizer = None):
        self.config = config
        self.tok = tokenizer or get_tokenizer()

    def judge(self, op_tokens: int, cache_hit: int, cache_miss: int, context_total: int) -> Tuple[bool, str]:
        """判定操作是否为低价值"""
        miss_contrib = cache_miss / op_tokens if op_tokens > 0 else 0
        ctx_ratio = op_tokens / context_total if context_total > 0 else 0

        if miss_contrib >= 0.95:
            return True, f"极高 miss 率 ({miss_contrib:.1%})"
        if miss_contrib >= self.config.MISS_THRESHOLD and ctx_ratio >= self.config.RATIO_THRESHOLD:
            return True, f"高 miss {miss_contrib:.1%} + 高占比 {ctx_ratio:.1%}"
        if cache_hit <= self.config.HIT_CEILING and ctx_ratio >= self.config.RATIO_THRESHOLD:
            return True, f"低 hit ({cache_hit}) + 高占比 {ctx_ratio:.1%}"

        return False, "高价值, 保留"


class TaskValueJudge:
    """任务级价值判定器 (5 维度加权评分)"""

    def __init__(self, config: Config, tokenizer: DeepSeekTokenizer = None):
        self.config = config.discard
        self.tok = tokenizer or get_tokenizer()

    def assess(self, requests: List[SubRequest], context_total: int, task_description: str = "") -> TaskVerdict:
        """判定任务价值"""
        if not requests:
            return TaskVerdict(False, "空任务", 1.0)

        task_tokens = sum(r.tokens for r in requests)
        total_hit = sum(r.cache_hit for r in requests)
        total_miss = sum(r.cache_miss for r in requests)
        total_out = sum(r.output_tokens for r in requests)
        n = len(requests)

        # 【快速通道】高输出密度 → 直接保留（即使 0 hit）
        # 避免第一次请求就被 discard：0 hit + exploration role + 大context占比 = 低分
        if task_tokens > 0:
            output_density = total_out / task_tokens
            if output_density >= 0.20:  # >20% 产出率 = 有意义的工作
                return TaskVerdict(False, f"高产出率 {output_density:.1%}, 保留", 0.60)

        # 5 维度评分
        s1 = self._score_cache_contrib(total_hit, total_miss)
        s2 = self._score_context_ratio(task_tokens, context_total)
        s3 = self._score_output_density(total_out, task_tokens)
        s4 = self._score_chain_depth(n)
        s5 = self._score_role_mix(requests)

        score = (
            s1 * self.config.w_cache_contrib
            + s2 * self.config.w_context_ratio
            + s3 * self.config.w_output_density
            + s4 * self.config.w_chain_depth
            + s5 * self.config.w_role_mix
        )

        # 判定
        if score >= self.config.score_threshold_high:
            return TaskVerdict(False, f"高价值 (score={score:.2f})", score)
        elif score < self.config.score_threshold_low:
            high_reqs = self._extract_high_value(requests, task_tokens)
            summary = self._generate_summary(requests, task_description, task_tokens, total_hit, total_miss)
            return TaskVerdict(True, f"低价值 (score={score:.2f})", score, high_value_requests=high_reqs, summary=summary)
        else:
            return TaskVerdict(False, f"可疑 (score={score:.2f}), 保留观察", score)

    def _score_cache_contrib(self, total_hit: int, total_miss: int) -> float:
        """缓存贡献率 → [0, 1]"""
        total = total_hit + total_miss
        if total == 0:
            return 0.5
        rate = total_hit / total
        return min(1.0, rate / self.config.cache_hit_rate_good)

    def _score_context_ratio(self, task_tokens: int, context_total: int) -> float:
        """上下文占比 → [0, 1] (反向: 越低越好)"""
        if context_total == 0:
            return 0.5
        ratio = task_tokens / context_total
        if ratio >= self.config.context_ratio_max:
            return 0.0
        return 1.0 - (ratio / self.config.context_ratio_max)

    def _score_output_density(self, total_out: int, task_tokens: int) -> float:
        """输出密度 → [0, 1]"""
        if task_tokens == 0:
            return 0.0
        density = total_out / task_tokens
        return min(1.0, density / self.config.output_density_good)

    def _score_chain_depth(self, n_requests: int) -> float:
        """请求链深度 → [0, 1]"""
        if n_requests >= self.config.chain_depth_good:
            return 1.0
        return n_requests / self.config.chain_depth_good

    def _score_role_mix(self, requests: List[SubRequest]) -> float:
        """角色分布质量 → [0, 1]"""
        roles = {r.role for r in requests}
        has_quality = bool(roles & self.config.high_value_roles)
        all_explore = roles == {SubRequest.ROLE_EXPLORATION}

        if all_explore:
            return 0.1
        if has_quality:
            quality_count = len(roles & self.config.high_value_roles)
            return 0.7 + 0.3 * min(1.0, quality_count / 3)
        return 0.4

    def _extract_high_value(self, requests: List[SubRequest], task_tokens: int) -> List[SubRequest]:
        """从低价值任务中提取高价值子请求

        优先级: OUTPUT > ANALYZE > WRITE > 高缓存命中
        """
        extracted = []
        remaining = self.config.max_extract_tokens

        # 优先级分组
        priority_groups = [
            [r for r in requests if r.role == SubRequest.ROLE_OUTPUT],
            [r for r in requests if r.role == SubRequest.ROLE_ANALYZE],
            [r for r in requests if r.role == SubRequest.ROLE_WRITE],
            [r for r in requests if r.is_high_hit],
        ]

        for group in priority_groups:
            for r in group:
                if r in extracted:
                    continue
                if r.tokens <= remaining:
                    extracted.append(r)
                    remaining -= r.tokens

        return extracted

    def _generate_summary(self, requests: List[SubRequest], description: str,
                          task_tokens: int, total_hit: int, total_miss: int) -> str:
        """生成任务丢弃后的压缩摘要"""
        hit_rate = total_hit / (total_hit + total_miss) * 100 if (total_hit + total_miss) > 0 else 0
        n = len(requests)
        role_counts = {}
        for r in requests:
            role_counts[r.role] = role_counts.get(r.role, 0) + 1
        roles_str = ", ".join(f"{k}={v}" for k, v in sorted(role_counts.items()))

        return (
            f"[DISCARDED TASK] {description}\n"
            f"  requests={n} tokens={task_tokens} hit_rate={hit_rate:.1f}%\n"
            f"  roles: {roles_str}"
        )

    def explain(self, verdict: TaskVerdict) -> str:
        """生成判定解释"""
        status = "✗ DISCARD" if verdict.is_low_value else "✓ KEEP"
        lines = [
            f"{status} | score={verdict.score:.2f} | {verdict.reason}",
        ]
        if verdict.high_value_requests:
            tokens = sum(r.tokens for r in verdict.high_value_requests)
            lines.append(f"  Extracted: {len(verdict.high_value_requests)} requests, {tokens} tokens")
            for r in verdict.high_value_requests:
                label = r.label[:50] if r.label else "(no label)"
                lines.append(f"    [{r.role}] {label} ({r.tokens}t, hit={r.cache_hit})")
        if verdict.summary:
            lines.append(f"  Summary: {verdict.summary}")
        return "\n".join(lines)
