"""
tokenizer_poc.py — DeepSeek 分词器逆向 & 三区模型 Token 分析器

核心发现:
1. Tokenizer: BPE, 128,815 vocab, 从 HuggingFace deepseek-ai/DeepSeek-V3 获取
2. Chat template 开销: base 4 token + 每增加消息 +2 token (n>2)
3. 缓存粒度: 128 token 整数倍, < 128 不缓存
4. 本地 token 计数 = API 缓存命中 token 数 (不含 template 开销)
5. 三区模型: 永久冻结区(始终命中) + 暂时冻结区(大概率命中) + 自然增长区(miss)

用法:
  python Poc/tokenizer_poc.py              # 全量分析 (需要 API key)
  python Poc/tokenizer_poc.py --local      # 仅本地分析 (不需要 API)
  python Poc/tokenizer_poc.py --verify     # 验证本地 vs API 计数 (需要 API key)
"""

import json, os, sys, time
from urllib.request import Request, urlopen

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tokenizers import Tokenizer

# ══════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
TOKENIZER_PATH = os.path.join(CACHE_DIR, "tokenizer.json")

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

PRICING = {
    "deepseek-v4-flash": {"miss": 0.14, "hit": 0.0028, "out": 0.28},
    "deepseek-v4-pro":   {"miss": 0.435, "hit": 0.003625, "out": 0.87},
}

# ══════════════════════════════════════════════════════════
# Tokenizer 封装
# ══════════════════════════════════════════════════════════

class DeepSeekTokenizer:
    """DeepSeek V3/V4 分词器封装 (Tiktoken-based BPE)"""

    CACHE_GRANULARITY = 128   # 缓存最小粒度
    VOCAB_SIZE = 128815       # 词表大小

    def __init__(self):
        if not os.path.exists(TOKENIZER_PATH):
            raise FileNotFoundError(
                f"tokenizer.json not found at {TOKENIZER_PATH}\n"
                f"Download: https://huggingface.co/deepseek-ai/DeepSeek-V3/resolve/main/tokenizer.json"
            )
        self._tok = Tokenizer.from_file(TOKENIZER_PATH)

    def count(self, text: str) -> int:
        """返回文本的 token 数量"""
        return len(self._tok.encode(text).ids)

    def encode(self, text: str) -> list[int]:
        """返回 token ID 列表"""
        return self._tok.encode(text).ids

    def tokens(self, text: str) -> list[str]:
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

    def estimate_cache_hit(self, stable_prefix: str, full_input: str) -> tuple[int, int]:
        """
        估算缓存命中情况
        Returns: (hit_tokens, miss_tokens)
        """
        common = self.common_prefix_len(stable_prefix, full_input)
        hit = self.cache_hit_tokens(common)
        total = self.count(full_input)
        return hit, total - hit

# ══════════════════════════════════════════════════════════
# Chat Template 开销模型
# ══════════════════════════════════════════════════════════

class TemplateEstimator:
    """
    Chat template token 开销模型

    实测公式 (deepseek-v4-flash):
      api_prompt_tokens = sum(local_content_tokens) + overhead

      overhead = 4 (base) + 2 × max(0, n_messages - 2)

    示例:
      1 msg:  overhead = 4
      2 msgs: overhead = 4
      4 msgs: overhead = 8
      6 msgs: overhead = 12
      N msgs: overhead = 4 + 2×(N-2) for N > 2
    """

    BASE_OVERHEAD = 4
    PER_MESSAGE_OVERHEAD = 2  # 每多一条消息的增量 (N > 2)

    @staticmethod
    def estimate(messages: list[dict], content_tokens: int) -> int:
        """根据消息数量和内容 token 数估算 API prompt_tokens"""
        n = len(messages)
        overhead = TemplateEstimator.BASE_OVERHEAD
        if n > 2:
            overhead += TemplateEstimator.PER_MESSAGE_OVERHEAD * (n - 2)
        return content_tokens + overhead

    @staticmethod
    def content_tokens_from_api(api_prompt_tokens: int, n_messages: int) -> int:
        """从 API 返回的 prompt_tokens 反推内容 token 数"""
        overhead = TemplateEstimator.BASE_OVERHEAD
        if n_messages > 2:
            overhead += TemplateEstimator.PER_MESSAGE_OVERHEAD * (n_messages - 2)
        return api_prompt_tokens - overhead

# ══════════════════════════════════════════════════════════
# 三区模型分析器
# ══════════════════════════════════════════════════════════

class ContextAnalyzer:
    """
    ContextManager 三区模型 Token 分析器

    模型:
      请求 = [永久冻结区] + [暂时冻结区] + [自然增长区]
                ↑ 始终命中        ↑ 大概率命中      ↑ 大概率 miss

    缓存对齐:
      - 永久冻结区: 内容不变 → 始终命中, 成本 ≈ 0
      - 暂时冻结区: 内容稳定 → 后续命中, 成本低
      - 自然增长区: 频繁变化 → 每次 miss, 成本高
    """

    def __init__(self, tokenizer: DeepSeekTokenizer):
        self.tok = tokenizer
        self.zones: dict[str, dict] = {
            "permanent": {"name": "永久冻结区", "content": "", "tokens": 0},
            "temporary": {"name": "暂时冻结区", "content": "", "tokens": 0},
            "natural":   {"name": "自然增长区", "content": "", "tokens": 0},
        }

    def set_zone(self, zone_key: str, content: str):
        """设置区域内容"""
        self.zones[zone_key]["content"] = content
        self.zones[zone_key]["tokens"] = self.tok.count(content)

    def build_prefix(self) -> str:
        """构建 shared prefix (永久 + 暂时)"""
        return self.zones["permanent"]["content"] + self.zones["temporary"]["content"]

    def analyze(self) -> dict:
        """分析当前三区状态"""
        p = self.zones["permanent"]
        t = self.zones["temporary"]
        n = self.zones["natural"]

        total = p["tokens"] + t["tokens"] + n["tokens"]
        prefix_tokens = p["tokens"] + t["tokens"]

        # 缓存估算
        cache_hit = self.tok.cache_hit_tokens(prefix_tokens)
        cache_miss = total - cache_hit

        # 费用估算
        price = PRICING.get(MODEL, PRICING["deepseek-v4-flash"])
        cost_all_miss = total * price["miss"] / 1_000_000
        cost_with_cache = (cache_miss * price["miss"] + cache_hit * price["hit"]) / 1_000_000
        cost_saving = (cost_all_miss - cost_with_cache) / cost_all_miss * 100 if cost_all_miss > 0 else 0

        return {
            "zones": {
                "permanent": dict(p),
                "temporary": dict(t),
                "natural": dict(n),
            },
            "total_tokens": total,
            "prefix_tokens": prefix_tokens,
            "estimated_cache_hit": cache_hit,
            "estimated_cache_miss": cache_miss,
            "hit_rate": cache_hit / total * 100 if total > 0 else 0,
            "cost_all_miss": cost_all_miss,
            "cost_with_cache": cost_with_cache,
            "cost_saving_pct": cost_saving,
            "price_model": MODEL,
            "pricing": dict(price),
        }

    def report(self) -> str:
        """生成分析报告"""
        a = self.analyze()
        p, t, n = a["zones"]["permanent"], a["zones"]["temporary"], a["zones"]["natural"]

        lines = [
            "=" * 60,
            "三区模型 Token 分析报告",
            "=" * 60,
            f"  Model: {a['price_model']}",
            "",
            f"  {p['name']:12s}: {p['tokens']:6d} tokens  → 始终命中, 成本 ≈ $0",
            f"  {t['name']:12s}: {t['tokens']:6d} tokens  → 大概率命中",
            f"  {n['name']:12s}: {n['tokens']:6d} tokens  → 大概率 miss",
            f"  {'总计':12s}: {a['total_tokens']:6d} tokens",
            "",
            f"  Cache hit 估算:  {a['estimated_cache_hit']:6d} tokens ({a['hit_rate']:.1f}%)",
            f"  Cache miss 估算: {a['estimated_cache_miss']:6d} tokens",
            "",
            f"  费用 (全 miss):  ${a['cost_all_miss']:.6f}",
            f"  费用 (有缓存):  ${a['cost_with_cache']:.6f}",
            f"  节省:           {a['cost_saving_pct']:.1f}%",
            "",
            "  优化建议:",
        ]

        # 优化建议
        if n["tokens"] > p["tokens"] + t["tokens"]:
            lines.append("    ⚠ 自然增长区 > 冻结区之和 → 考虑压缩或回滚")
        if p["tokens"] < 128:
            lines.append(f"    ⚠ 永久冻结区 < 128 token → 不触发缓存, 补充到 ≥ 128 (当前 {p['tokens']})")
        if n["tokens"] > 500:
            lines.append(f"    ⚠ 自然增长区 {n['tokens']} tokens → 及时压缩末尾低价值操作")
        if a["hit_rate"] < 50:
            lines.append(f"    ⚠ 缓存命中率 {a['hit_rate']:.1f}% < 50% → 检查前缀稳定性")

        return "\n".join(lines)

# ══════════════════════════════════════════════════════════
# 低价值操作判定
# ══════════════════════════════════════════════════════════

class ValueJudge:
    """
    低价值操作判定器

    判定模型:
      isLowValue = (missContribution >= 0.9 AND contextRatio >= 0.15)
                OR (hitContribution <= 1000 AND contextRatio >= 0.15)
                OR (missContribution >= 0.95)
    """

    MISS_THRESHOLD = 0.9
    RATIO_THRESHOLD = 0.15
    HIT_CEILING = 1000

    def __init__(self, tokenizer: DeepSeekTokenizer):
        self.tok = tokenizer

    def judge(self, op_tokens: int, cache_hit: int, cache_miss: int,
              context_total: int) -> tuple[bool, str]:
        """
        判定操作是否为低价值
        Returns: (is_low_value, reason)
        """
        miss_contrib = cache_miss / op_tokens if op_tokens > 0 else 0
        hit_contrib = cache_hit
        ctx_ratio = op_tokens / context_total if context_total > 0 else 0

        if miss_contrib >= 0.95:
            return True, f"极高 miss 率 ({miss_contrib:.1%})"
        if miss_contrib >= self.MISS_THRESHOLD and ctx_ratio >= self.RATIO_THRESHOLD:
            return True, f"高 miss {miss_contrib:.1%} + 高占比 {ctx_ratio:.1%}"
        if hit_contrib <= self.HIT_CEILING and ctx_ratio >= self.RATIO_THRESHOLD:
            return True, f"低 hit ({hit_contrib}) + 高占比 {ctx_ratio:.1%}"

        return False, "高价值, 保留"

# ══════════════════════════════════════════════════════════
# 任务级价值判定
# ══════════════════════════════════════════════════════════

class SubRequest:
    """任务中的单次请求记录"""

    ROLE_EXPLORATION = "exploration"   # 探索: grep, find, ls
    ROLE_READ        = "read"          # 读取: read file, cat
    ROLE_ANALYZE     = "analyze"       # 分析: chat 无 tool call
    ROLE_OUTPUT      = "output"        # 输出: 最终回复
    ROLE_WRITE       = "write"         # 写入: write/edit file

    def __init__(self, role: str, tokens: int, cache_hit: int, cache_miss: int,
                 output_tokens: int = 0, label: str = ""):
        self.role = role
        self.tokens = tokens
        self.cache_hit = cache_hit
        self.cache_miss = cache_miss
        self.output_tokens = output_tokens
        self.label = label

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

class TaskVerdict:
    """任务价值判定结果"""

    def __init__(self, is_low_value: bool, reason: str, score: float,
                 high_value_requests: list[SubRequest] = None,
                 summary: str = ""):
        self.is_low_value = is_low_value
        self.reason = reason
        self.score = score          # 0.0 ~ 1.0, 越高越有价值
        self.high_value_requests = high_value_requests or []
        self.summary = summary      # 丢弃时生成的压缩摘要

class TaskValueJudge:
    """
    任务级价值判定器

    判定模型:
      任务价值 = f(缓存贡献, 上下文占比, 输出密度, 请求链深度, 角色分布)

    判定流程:
      1. 聚合所有子请求的 cache 指标
      2. 计算 5 个维度的得分
      3. 加权求和 → 总分
      4. 低于阈值 → 低价值, 提取高价值子请求 + 生成摘要
      5. 高于阈值 → 高价值, 整任务保留

    配置可通过 TaskValueJudge.Config 调整。
    """

    class Config:
        """可调配置 (生产环境通过 yaml/json 覆盖)"""
        # 判定阈值
        SCORE_THRESHOLD_LOW  = 0.35   # < 此值 → 低价值, 丢弃
        SCORE_THRESHOLD_HIGH = 0.65   # > 此值 → 高价值, 整保留
                                      # 之间 → 可疑, 保留但标记

        # 维度权重 (5 维度, 总和 = 1.0)
        W_CACHE_CONTRIB  = 0.30   # 缓存贡献率
        W_CONTEXT_RATIO  = 0.20   # 上下文占比 (反向: 越大越低分)
        W_OUTPUT_DENSITY = 0.20   # 输出密度
        W_CHAIN_DEPTH    = 0.15   # 请求链深度
        W_ROLE_MIX       = 0.15   # 角色分布质量

        # 子维度参数
        CACHE_HIT_RATE_GOOD   = 0.5    # 命中率 > 此值 → 满分
        CONTEXT_RATIO_MAX     = 0.3    # 占比 > 此值 → 0 分
        OUTPUT_DENSITY_GOOD   = 0.3    # output/total > 此值 → 满分
        CHAIN_DEPTH_GOOD      = 3      # 请求数 > 此值 → 满分

        # 高价值角色 (这些角色的子请求在丢弃时优先保留)
        HIGH_VALUE_ROLES = {SubRequest.ROLE_ANALYZE, SubRequest.ROLE_OUTPUT,
                            SubRequest.ROLE_WRITE}

        # 提取限制
        MAX_EXTRACT_TOKENS = 500   # 从低价值任务中最多提取的 token 数

    def __init__(self, tokenizer: DeepSeekTokenizer, config: Config = None):
        self.tok = tokenizer
        self.config = config or TaskValueJudge.Config()

    # ── 判定入口 ──────────────────────────────────────

    def assess(self, requests: list[SubRequest], context_total: int,
               task_description: str = "") -> TaskVerdict:
        """
        判定一个任务的价值
        Args:
          requests: 任务中所有子请求
          context_total: 当前上下文总 token 数
          task_description: 任务描述 (用于生成摘要)
        """
        if not requests:
            return TaskVerdict(False, "空任务", 1.0)

        task_tokens = sum(r.tokens for r in requests)
        total_hit   = sum(r.cache_hit for r in requests)
        total_miss  = sum(r.cache_miss for r in requests)
        total_out   = sum(r.output_tokens for r in requests)
        n = len(requests)

        # 5 维度评分
        s1 = self._score_cache_contrib(total_hit, total_miss)
        s2 = self._score_context_ratio(task_tokens, context_total)
        s3 = self._score_output_density(total_out, task_tokens)
        s4 = self._score_chain_depth(n)
        s5 = self._score_role_mix(requests)

        score = (
            s1 * self.config.W_CACHE_CONTRIB +
            s2 * self.config.W_CONTEXT_RATIO +
            s3 * self.config.W_OUTPUT_DENSITY +
            s4 * self.config.W_CHAIN_DEPTH +
            s5 * self.config.W_ROLE_MIX
        )

        # 判定
        if score >= self.config.SCORE_THRESHOLD_HIGH:
            return TaskVerdict(False, f"高价值 (score={score:.2f})", score)
        elif score < self.config.SCORE_THRESHOLD_LOW:
            high_reqs = self._extract_high_value(requests, task_tokens)
            summary = self._generate_summary(requests, task_description, task_tokens,
                                             total_hit, total_miss)
            return TaskVerdict(True, f"低价值 (score={score:.2f})", score,
                              high_value_requests=high_reqs, summary=summary)
        else:
            return TaskVerdict(False, f"可疑 (score={score:.2f}), 保留观察", score)

    # ── 5 维度评分 ────────────────────────────────────

    def _score_cache_contrib(self, total_hit: int, total_miss: int) -> float:
        """缓存贡献率 → [0, 1]"""
        total = total_hit + total_miss
        if total == 0:
            return 0.5
        rate = total_hit / total
        return min(1.0, rate / self.config.CACHE_HIT_RATE_GOOD)

    def _score_context_ratio(self, task_tokens: int, context_total: int) -> float:
        """上下文占比 → [0, 1] (越低越好, 反向)"""
        if context_total == 0:
            return 0.5
        ratio = task_tokens / context_total
        if ratio >= self.config.CONTEXT_RATIO_MAX:
            return 0.0
        return 1.0 - (ratio / self.config.CONTEXT_RATIO_MAX)

    def _score_output_density(self, total_out: int, task_tokens: int) -> float:
        """输出密度 → [0, 1] (输出 token 占比)"""
        if task_tokens == 0:
            return 0.0
        density = total_out / task_tokens
        return min(1.0, density / self.config.OUTPUT_DENSITY_GOOD)

    def _score_chain_depth(self, n_requests: int) -> float:
        """请求链深度 → [0, 1] (更多请求 → 更深分析)"""
        if n_requests >= self.config.CHAIN_DEPTH_GOOD:
            return 1.0
        return n_requests / self.config.CHAIN_DEPTH_GOOD

    def _score_role_mix(self, requests: list[SubRequest]) -> float:
        """角色分布质量 → [0, 1]"""
        roles = [r.role for r in requests]
        unique = set(roles)

        # 有 analyze/output/write → 高质量
        has_quality = bool(unique & self.config.HIGH_VALUE_ROLES)
        # 全是 exploration → 低质量
        all_explore = unique == {SubRequest.ROLE_EXPLORATION}

        if all_explore:
            return 0.1
        if has_quality:
            return 0.7 + 0.3 * min(1.0, len(unique & self.config.HIGH_VALUE_ROLES) / 3)
        return 0.4

    # ── 高价值子请求提取 ──────────────────────────────

    def _extract_high_value(self, requests: list[SubRequest],
                            task_tokens: int) -> list[SubRequest]:
        """
        从低价值任务中提取高价值子请求

        规则 (按优先级):
          1. ROLE_OUTPUT 子请求 → 保留 (任务结论)
          2. ROLE_ANALYZE 子请求 → 保留 (分析过程)
          3. ROLE_WRITE 子请求 → 保留 (代码产出)
          4. 高缓存命中 (is_high_hit) 的子请求 → 保留 (复用价值)
          5. 不超过 MAX_EXTRACT_TOKENS
        """
        extracted = []
        remaining = self.config.MAX_EXTRACT_TOKENS

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

    def _generate_summary(self, requests: list[SubRequest], description: str,
                          task_tokens: int, total_hit: int, total_miss: int) -> str:
        """生成任务丢弃后的压缩摘要"""
        hit_rate = total_hit / (total_hit + total_miss) * 100 if (total_hit + total_miss) > 0 else 0
        n = len(requests)
        roles = [r.role for r in requests]
        role_summary = ", ".join(f"{r}={roles.count(r)}" for r in sorted(set(roles)))

        return (
            f"[DISCARDED TASK] {description}\n"
            f"  requests={n} tokens={task_tokens} hit_rate={hit_rate:.1f}%\n"
            f"  roles: {role_summary}"
        )

    # ── 报告 ──────────────────────────────────────────

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
                lines.append(f"    [{r.role}] {r.label[:60]} ({r.tokens}t, hit={r.cache_hit})")
        if verdict.summary:
            lines.append(f"  Summary: {verdict.summary}")
        return "\n".join(lines)

# ══════════════════════════════════════════════════════════
# 验证实验
# ══════════════════════════════════════════════════════════

def verify_template(tok: DeepSeekTokenizer):
    """验证 chat template 开销公式"""
    if not API_KEY:
        print("[skip] 需要 API key")
        return

    print("验证: Chat Template 开销")
    print("-" * 40)

    test_msgs = [
        ([{"role": "user", "content": "ab"}], "1 user"),
        ([{"role": "system", "content": "ab"}, {"role": "user", "content": "ab"}], "sys+user"),
        ([{"role": "system", "content": "ab"}, {"role": "user", "content": "ab"},
          {"role": "assistant", "content": "ab"}, {"role": "user", "content": "ab"}], "sys+user+asst+user"),
    ]

    for messages, label in test_msgs:
        local = sum(tok.count(m["content"]) for m in messages)
        body = {"model": MODEL, "messages": messages, "max_tokens": 5}
        data = json.dumps(body).encode()
        req = Request(BASE_URL.rstrip("/") + "/chat/completions", data=data, headers={
            "Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
        with urlopen(req, timeout=30) as resp:
            api = json.loads(resp.read())["usage"]["prompt_tokens"]

        est = TemplateEstimator.estimate(messages, local)
        print(f"  {label:20s}: local={local} api={api} est={est} err={api-est}")
        time.sleep(0.3)

def verify_cache_boundary(tok: DeepSeekTokenizer):
    """验证 128 token 缓存边界"""
    if not API_KEY:
        print("[skip] 需要 API key")
        return

    print("\n验证: 128 Token 缓存边界")
    print("-" * 40)

    # 构建不同长度的 system prompt
    targets = [60, 100, 128, 200, 256]
    pad = "You follow best practices. "

    for target in targets:
        prompt = "You are a coding assistant. "
        while tok.count(prompt) < target:
            prompt += pad

        local = tok.count(prompt)

        # 第 1 次 (构建缓存)
        def call(sys_msg, usr_msg):
            body = {"model": MODEL, "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": usr_msg}], "max_tokens": 5}
            data = json.dumps(body).encode()
            req = Request(BASE_URL.rstrip("/") + "/chat/completions", data=data, headers={
                "Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())["usage"]

        call(prompt, "warmup")
        time.sleep(1)

        # 第 2 次 (验证命中)
        u = call(prompt, "hi")
        hit = u.get("prompt_cache_hit_tokens", 0)
        miss = u.get("prompt_cache_miss_tokens", u["prompt_tokens"])

        expected_hit = tok.cache_hit_tokens(local)
        print(f"  target={target:3d} local={local:3d} hit={hit:3d} miss={miss:3d} "
              f"expected_hit={expected_hit:3d} {'OK' if hit == expected_hit else 'MISMATCH'}")
        time.sleep(1)

# ══════════════════════════════════════════════════════════
# 演示
# ══════════════════════════════════════════════════════════

def demo_task_judge(tok: DeepSeekTokenizer):
    """任务级价值判定演示"""
    judge = TaskValueJudge(tok)

    print("=" * 60)
    print("任务级价值判定演示")
    print("=" * 60)

    context_total = 500

    scenarios = [
        {
            "desc": "探索任务: grep 搜索 + 读文件",
            "requests": [
                SubRequest(SubRequest.ROLE_EXPLORATION, 200, 0, 200, 0, "grep -n degrade *.ts"),
                SubRequest(SubRequest.ROLE_READ,        300, 0, 300, 0, "read leveldefine_xzmp.ts"),
                SubRequest(SubRequest.ROLE_READ,        250, 0, 250, 0, "read cmmonthcard_xzmp.ts"),
                SubRequest(SubRequest.ROLE_ANALYZE,     100, 10, 90, 80, "分析 grep 结果"),
            ],
        },
        {
            "desc": "深度分析: 读代码 → 分析 → 写文档",
            "requests": [
                SubRequest(SubRequest.ROLE_READ,     150, 80, 70,  0,  "read leveldefine_xzmp.ts"),
                SubRequest(SubRequest.ROLE_READ,     150, 80, 70,  0,  "read cmmonthcard_xzmp.ts"),
                SubRequest(SubRequest.ROLE_ANALYZE,  120, 50, 70,  90, "对比降级策略"),
                SubRequest(SubRequest.ROLE_OUTPUT,   100, 40, 60,  100,"最终文档输出"),
            ],
        },
        {
            "desc": "快速查询: 单次简单问题",
            "requests": [
                SubRequest(SubRequest.ROLE_ANALYZE, 30, 20, 10, 25, "什么是 async/await?"),
            ],
        },
        {
            "desc": "纯探索: 大量 grep/ls 无分析",
            "requests": [
                SubRequest(SubRequest.ROLE_EXPLORATION, 180, 0, 180, 0, "grep function *.ts"),
                SubRequest(SubRequest.ROLE_EXPLORATION, 150, 0, 150, 0, "grep class *.ts"),
                SubRequest(SubRequest.ROLE_EXPLORATION, 120, 0, 120, 0, "ls -la src/"),
                SubRequest(SubRequest.ROLE_EXPLORATION, 200, 0, 200, 0, "find . -name '*.ts'"),
            ],
        },
        {
            "desc": "写入任务: 读 → 分析 → 写代码",
            "requests": [
                SubRequest(SubRequest.ROLE_READ,     100, 80, 20,  0,  "read config.ts"),
                SubRequest(SubRequest.ROLE_ANALYZE,   80, 60, 20,  60, "设计修改方案"),
                SubRequest(SubRequest.ROLE_WRITE,     120, 40, 80,  0,  "edit leveldefine_xzmp.ts"),
                SubRequest(SubRequest.ROLE_OUTPUT,     60, 30, 30,  50, "修改完成汇报"),
            ],
        },
    ]

    for i, scenario in enumerate(scenarios):
        print(f"\n{'─' * 40}")
        print(f"场景 {i+1}: {scenario['desc']}")
        print(f"{'─' * 40}")

        reqs = scenario["requests"]
        task_tokens = sum(r.tokens for r in reqs)
        total_hit = sum(r.cache_hit for r in reqs)
        total_miss = sum(r.cache_miss for r in reqs)
        total_out = sum(r.output_tokens for r in reqs)

        print(f"  请求数: {len(reqs)}  总 token: {task_tokens}  "
              f"hit={total_hit} miss={total_miss} out={total_out}")
        print(f"  上下文占比: {task_tokens/context_total:.1%}  "
              f"命中率: {total_hit/(total_hit+total_miss)*100 if (total_hit+total_miss) else 0:.1f}%")

        for r in reqs:
            print(f"    [{r.role:11s}] {r.label[:50]:50s}  {r.tokens:4d}t  hit={r.cache_hit:3d} miss={r.cache_miss:3d}")

        verdict = judge.assess(reqs, context_total, scenario["desc"])
        print(f"\n  → {judge.explain(verdict)}")

    # 配置调整演示
    print(f"\n{'=' * 60}")
    print("配置灵敏度: 调整 SCORE_THRESHOLD_LOW")
    print(f"{'=' * 60}")

    explore_reqs = [
        SubRequest(SubRequest.ROLE_EXPLORATION, 200, 0, 200, 0, "grep -n degrade *.ts"),
        SubRequest(SubRequest.ROLE_READ,        300, 0, 300, 0, "read leveldefine_xzmp.ts"),
        SubRequest(SubRequest.ROLE_ANALYZE,     100, 10, 90, 80, "分析结果"),
    ]

    for threshold in [0.25, 0.35, 0.45]:
        config = TaskValueJudge.Config()
        config.SCORE_THRESHOLD_LOW = threshold
        j = TaskValueJudge(tok, config)
        v = j.assess(explore_reqs, 500, "探索任务")
        print(f"  threshold={threshold:.2f} → {v.is_low_value} (score={v.score:.2f}) {v.reason}")


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════

def main():
    tok = DeepSeekTokenizer()
    print(f"DeepSeek Tokenizer: {tok.VOCAB_SIZE} vocab, cache granularity={tok.CACHE_GRANULARITY}")

    mode = sys.argv[1] if len(sys.argv) > 1 else "--full"

    if mode == "--task":
        # 任务级价值判定演示
        demo_task_judge(tok)

    elif mode == "--local":
        # 纯本地分析
        print("\n(本地模式: 仅分析, 不发 API 请求)\n")

        # 模拟三区内容
        analyzer = ContextAnalyzer(tok)
        analyzer.set_zone("permanent",
            "You are a specialized AI agent for the Dog-Rider project. "
            "You analyze TypeScript code in CP server modules. "
            "You follow namespace conventions with Business, CommonFuncs, interf, TestTool. "
            "You use async/await patterns, Redis locks, MySQL storage.")
        analyzer.set_zone("temporary",
            "## Already Read\n"
            "### leveldefine_xzmp.ts: player level tracking, degradation logic\n"
            "### cmmonthcard_xzmp.ts: monthly card purchase, query\n")
        analyzer.set_zone("natural",
            "## Current Task\n"
            "Compare degradation strategies between leveldefine and cmmonthcard.\n"
            "## Tool Output\n"
            "$ grep degrade *.ts → 42: degradeDays config, 89: degrade check\n")

        print(analyzer.report())

        # 前缀匹配演示
        print("\n前缀匹配演示:")
        a = "You are a specialized AI agent. Analyze leveldefine."
        b = "You are a specialized AI agent. Write cmmonthcard docs."
        common = tok.common_prefix_len(a, b)
        print(f"  A: {tok.count(a)} tokens")
        print(f"  B: {tok.count(b)} tokens")
        print(f"  公共前缀: {common} tokens")
        print(f"  缓存命中: {tok.cache_hit_tokens(common)} tokens (128 对齐)")
        print(f"  A 唯一后缀: {tok.count(a) - common} tokens (miss)")
        print(f"  B 唯一后缀: {tok.count(b) - common} tokens (miss)")

        # 价值判定演示
        print("\n低价值判定演示:")
        judge = ValueJudge(tok)
        context_total = 500
        ops = [
            (200, 10, 190, "高 miss 操作 (grep 结果)"),
            (50,  45,   5, "高命中操作 (稳定内容)"),
            (100, 0,  100, "全 miss 操作 (探索性)"),
        ]
        for tokens, hit, miss, desc in ops:
            low, reason = judge.judge(tokens, hit, miss, context_total)
            print(f"  {desc}: {tokens}t hit={hit} miss={miss} → {'低价值' if low else '高价值'} ({reason})")

    elif mode == "--verify":
        # 验证模式
        if not API_KEY:
            print("Error: DEEPSEEK_API_KEY not set.")
            sys.exit(1)
        verify_template(tok)
        verify_cache_boundary(tok)

    else:
        # 全量模式
        if not API_KEY:
            print("Error: DEEPSEEK_API_KEY not set. Use --local for offline analysis.")
            sys.exit(1)
        verify_template(tok)
        verify_cache_boundary(tok)

        # 三区分析
        print("\n" + "=" * 60)
        analyzer = ContextAnalyzer(tok)
        analyzer.set_zone("permanent",
            "You are a specialized AI agent for the Dog-Rider project. "
            "You analyze TypeScript code in CP server modules.")
        analyzer.set_zone("temporary",
            "## Read: leveldefine_xzmp.ts, cmmonthcard_xzmp.ts\n")
        analyzer.set_zone("natural",
            "## Task: Compare degradation strategies\n"
            "## Tool: grep -n degrade *.ts → found 5 matches\n")
        print(analyzer.report())

if __name__ == "__main__":
    main()
