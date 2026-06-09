"""任务级丢弃策略单元测试

覆盖核心场景:
- 0 hit 单请求任务 (当前遇到的问题)
- 全 hit 高价值任务
- 纯 exploration 任务
- 多工具调用任务
- 边界值测试
"""
import pytest

from src.config import Config
from src.discard import SubRequest, TaskValueJudge, TaskVerdict


@pytest.fixture
def judge():
    """创建默认配置的判定器"""
    config = Config()
    return TaskValueJudge(config)


def assess_score(judge, requests, context_total=1000):
    """辅助函数：仅返回 score，用于调试和断言"""
    verdict = judge.assess(requests, context_total)
    return verdict.score, verdict


# ========== 核心问题复现：0 hit 被 discard ==========

def test_single_request_zero_hit_high_output_kept(judge):
    """场景：0 hit 但高产出率（>=20%）→ 快速通道保留"""
    req = SubRequest(role="exploration", tokens=500, cache_hit=0, cache_miss=500, output_tokens=100)  # 20%
    score, verdict = assess_score(judge, [req])

    print(f"\n=== 0-hit 单请求 (20% 产出) ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    # 命中快速通道：score 被设置为 0.60
    assert verdict.is_low_value is False, "0-hit + 高产出 不应被丢弃"


def test_single_request_zero_hit_low_output_discarded(judge):
    """场景：0 hit 且低产出率（<20%）→ 正常评估，可能丢弃"""
    req = SubRequest(role="exploration", tokens=500, cache_hit=0, cache_miss=500, output_tokens=50)  # 10%
    score, verdict = assess_score(judge, [req])

    print(f"\n=== 0-hit 单请求 (10% 产出) ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    # 纯 exploration + 低产出 → 应该被丢弃
    assert verdict.is_low_value is True, "0-hit + 低产出 可被丢弃"


def test_single_request_zero_hit_context_small(judge):
    """上下文较小时，0 hit 的占比问题，但产出率触发快速通道"""
    req = SubRequest(role="exploration", tokens=200, cache_hit=0, cache_miss=200, output_tokens=80)  # 40%
    score, verdict = assess_score(judge, [req], context_total=500)

    print(f"\n=== 0-hit (context 500, 产出 40%) ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")

    # 产出率 40% → 快速通道保留
    assert verdict.is_low_value is False


# ========== 各维度单独测试 ==========

def test_score_cache_contrib(judge):
    """维度1：缓存贡献率"""
    # 100% hit
    s = judge._score_cache_contrib(500, 0)
    assert s == 1.0

    # 50% hit (正好是 good 线)
    s = judge._score_cache_contrib(250, 250)
    assert s == 1.0

    # 0% hit → 0
    s = judge._score_cache_contrib(0, 500)
    assert s == 0.0


def test_score_context_ratio(judge):
    """维度2：上下文占比"""
    # 0% → 1.0
    s = judge._score_context_ratio(0, 1000)
    assert s == 1.0

    # 15% (good 线) → 0.5
    s = judge._score_context_ratio(150, 1000)
    assert s == pytest.approx(0.5, 0.01)

    # >= 30% → 0
    s = judge._score_context_ratio(300, 1000)
    assert s == 0.0


def test_score_output_density(judge):
    """维度3：输出密度"""
    # 30% (good 线) → 1.0
    s = judge._score_output_density(150, 500)
    assert s == 1.0

    # 60% → 封顶 1.0
    s = judge._score_output_density(300, 500)
    assert s == 1.0

    # 0% → 0
    s = judge._score_output_density(0, 500)
    assert s == 0.0


def test_score_chain_depth(judge):
    """维度4：请求链深度"""
    # 3 (good 线) → 1.0
    s = judge._score_chain_depth(3)
    assert s == 1.0

    # 1 → 0.333
    s = judge._score_chain_depth(1)
    assert s == pytest.approx(1/3, 0.01)


def test_score_role_mix(judge):
    """维度5：角色分布"""
    # 纯 exploration → 0.1
    reqs = [SubRequest(role="exploration", tokens=100, cache_hit=0, cache_miss=100)]
    s = judge._score_role_mix(reqs)
    assert s == 0.1

    # 有 output → 高质量
    reqs = [SubRequest(role="output", tokens=100, cache_hit=0, cache_miss=100)]
    s = judge._score_role_mix(reqs)
    assert s >= 0.7

    # 有 analyze + output → 更高分
    reqs = [
        SubRequest(role="analyze", tokens=100, cache_hit=0, cache_miss=100),
        SubRequest(role="output", tokens=100, cache_hit=0, cache_miss=100),
    ]
    s = judge._score_role_mix(reqs)
    assert s > 0.7


# ========== 典型场景集成测试 ==========

def test_pure_exploration_low_value(judge):
    """纯 exploration 任务 → 低价值"""
    reqs = [
        SubRequest(role="exploration", tokens=100, cache_hit=0, cache_miss=100, output_tokens=10),
        SubRequest(role="exploration", tokens=120, cache_hit=0, cache_miss=120, output_tokens=15),
    ]
    score, verdict = assess_score(judge, reqs)

    print(f"\n=== 纯 exploration ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")

    # 纯 exploration 应该被标记为低价值
    assert score < judge.config.score_threshold_low


def test_write_operation_high_value(judge):
    """write 操作 → 高价值"""
    req = SubRequest(role="write", tokens=400, cache_hit=0, cache_miss=400, output_tokens=200)
    score, verdict = assess_score(judge, [req])

    print(f"\n=== write 操作 ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")

    # write 是高质量角色，不应该被丢弃
    assert verdict.is_low_value is False


def test_output_operation_high_value(judge):
    """output 操作 → 高价值"""
    req = SubRequest(role="output", tokens=300, cache_hit=0, cache_miss=300, output_tokens=500)
    score, verdict = assess_score(judge, [req])

    print(f"\n=== output 操作 ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")

    # output 是高质量角色，不应该被丢弃
    assert verdict.is_low_value is False


def test_full_hit_multi_turn(judge):
    """全 hit 多轮 → 高价值"""
    reqs = [
        SubRequest(role="read", tokens=500, cache_hit=500, cache_miss=0, output_tokens=50),
        SubRequest(role="analyze", tokens=520, cache_hit=520, cache_miss=0, output_tokens=80),
        SubRequest(role="output", tokens=550, cache_hit=550, cache_miss=0, output_tokens=300),
    ]
    score, verdict = assess_score(judge, reqs, context_total=2000)

    print(f"\n=== 全 hit 多轮 ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")

    assert verdict.is_low_value is False


def test_typical_code_search_pattern(judge):
    """典型代码搜索：grep + read (0 hit, 2 turns)"""
    reqs = [
        SubRequest(role="read", tokens=300, cache_hit=0, cache_miss=300, output_tokens=50),
        SubRequest(role="read", tokens=400, cache_hit=0, cache_miss=400, output_tokens=80),
    ]
    score, verdict = assess_score(judge, reqs, context_total=1500)

    print(f"\n=== 两次 read (0 hit) ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")

    # 有产出，不应该被丢弃
    assert verdict.is_low_value is False


def test_boundary_score_suspicious(judge):
    """边界：可疑区域 (0.40 ~ 0.65) → 不丢弃"""
    # 构造一个刚好在中间区域的案例
    reqs = [
        SubRequest(role="exploration", tokens=200, cache_hit=0, cache_miss=200, output_tokens=40),
        SubRequest(role="output", tokens=250, cache_hit=100, cache_miss=150, output_tokens=150),
    ]
    score, verdict = assess_score(judge, reqs, context_total=1000)

    print(f"\n=== 边界: 可疑区域 ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")

    assert judge.config.score_threshold_low <= score < judge.config.score_threshold_high
    assert verdict.is_low_value is False  # 可疑区域不丢弃


# ========== 极端情况 ==========

def test_empty_task(judge):
    """空任务"""
    score, verdict = assess_score(judge, [])
    assert verdict.is_low_value is False


def test_extremely_large_context(judge):
    """上下文很大，任务占比很小"""
    req = SubRequest(role="exploration", tokens=50, cache_hit=0, cache_miss=50, output_tokens=10)
    score, verdict = assess_score(judge, [req], context_total=10000)

    print(f"\n=== 大上下文，小任务 ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")

    # 占比很小，不应该丢弃
    assert verdict.is_low_value is False


def test_all_explore_with_high_output(judge):
    """纯 exploration 但输出密度高 → 快速通道"""
    req = SubRequest(role="exploration", tokens=200, cache_hit=0, cache_miss=200, output_tokens=200)  # 100%
    score, verdict = assess_score(judge, [req], context_total=1000)

    print(f"\n=== 纯 exploration，高输出 (100%) ===")
    print(f"  score: {score:.3f}")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    # 命中快速通道，不丢弃
    assert verdict.is_low_value is False, f"高输出任务不应丢弃，score={score:.3f}"
    assert "高产出率" in verdict.reason


# ========== 调试辅助：5 维度 breakdown ==========

def print_score_breakdown(judge, requests, context_total=1000):
    """打印各维度得分明细（调试用）"""
    task_tokens = sum(r.tokens for r in requests)
    total_hit = sum(r.cache_hit for r in requests)
    total_miss = sum(r.cache_miss for r in requests)
    total_out = sum(r.output_tokens for r in requests)
    n = len(requests)

    s1 = judge._score_cache_contrib(total_hit, total_miss)
    s2 = judge._score_context_ratio(task_tokens, context_total)
    s3 = judge._score_output_density(total_out, task_tokens)
    s4 = judge._score_chain_depth(n)
    s5 = judge._score_role_mix(requests)

    cfg = judge.config
    score = s1*cfg.w_cache_contrib + s2*cfg.w_context_ratio + s3*cfg.w_output_density + s4*cfg.w_chain_depth + s5*cfg.w_role_mix

    print(f"\n  ┌───────────────────────────────────────┐")
    print(f"  │  SCORE BREAKDOWN                       │")
    print(f"  ├───────────────────────────────────────┤")
    print(f"  │  s1 cache   : {s1:.3f} * {cfg.w_cache_contrib:.2f} = {s1*cfg.w_cache_contrib:.3f} │")
    print(f"  │  s2 context : {s2:.3f} * {cfg.w_context_ratio:.2f} = {s2*cfg.w_context_ratio:.3f} │")
    print(f"  │  s3 output  : {s3:.3f} * {cfg.w_output_density:.2f} = {s3*cfg.w_output_density:.3f} │")
    print(f"  │  s4 depth   : {s4:.3f} * {cfg.w_chain_depth:.2f} = {s4*cfg.w_chain_depth:.3f} │")
    print(f"  │  s5 role    : {s5:.3f} * {cfg.w_role_mix:.2f} = {s5*cfg.w_role_mix:.3f} │")
    print(f"  ├───────────────────────────────────────┤")
    print(f"  │  TOTAL      : {score:.3f}                   │")
    print(f"  │  Threshold  : <{cfg.score_threshold_low:.2f} discard, <{cfg.score_threshold_high:.2f} keep │")
    print(f"  └───────────────────────────────────────┘")
    return score


def test_zero_hit_breakdown(judge):
    """0-hit 单请求：各维度明细"""
    req = SubRequest(role="exploration", tokens=500, cache_hit=0, cache_miss=500, output_tokens=100)
    score = print_score_breakdown(judge, [req])

    # 验证各维度：
    # s1 = 0 (0 hit) * 0.30 = 0
    # s2 = 1 - (500/1000)/0.3 = ~-0.666 → cap at 0
    # s3 = (100/500)/0.3 = 0.666 * 0.20 = 0.133
    # s4 = 1/3 * 0.15 = 0.05
    # s5 = 0.1 (纯 exploration) * 0.15 = 0.015
    # total ≈ 0.20 (低于 0.40 阈值)

    print(f"\n  → s1 dominates: s1=0, s3+s4+s5={score:.3f}")
    print(f"  → 这就是 0-hit 被丢弃的原因：s1 权重太大")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
