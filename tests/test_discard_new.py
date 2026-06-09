"""新丢弃策略测试

核心逻辑：丢弃高命中任务（便宜重建），保留低命中任务（已付代价）
"""
import pytest

from src.config import Config
from src.discard import SubRequest, TaskValueJudge


@pytest.fixture
def judge():
    config = Config()
    return TaskValueJudge(config)


# ========== 规则 1: miss > 600 → 保留 ==========

def test_miss_over_600_keep(judge):
    """miss > 600 → 不丢弃（已付大代价）"""
    # 即使命中率 99%，但 miss 绝对值大 → 保留
    req = SubRequest(role="read", tokens=50000, cache_hit=49400, cache_miss=601, output_tokens=100)
    verdict = judge.assess([req], 100000)

    print(f"\n=== miss=601 (over 600) ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    assert verdict.is_low_value is False, "miss>600 不应丢弃"


def test_miss_under_600_check_further(judge):
    """miss ≤ 600 → 继续检查"""
    req = SubRequest(role="read", tokens=50000, cache_hit=49400, cache_miss=600, output_tokens=100)
    verdict = judge.assess([req], 100000)

    print(f"\n=== miss=600 (boundary) ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    # 600 继续往下走


# ========== 规则 2a: miss/context < 1% → 保留 ==========

def test_miss_ratio_under_1_percent_keep(judge):
    """miss/context < 1% → 不丢弃（太小可忽略）"""
    # miss=50, context=10000 → 0.5% < 1% → 保留
    req = SubRequest(role="read", tokens=10000, cache_hit=9950, cache_miss=50, output_tokens=100)
    verdict = judge.assess([req], 10000)

    print(f"\n=== miss/context = 0.5% ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    assert verdict.is_low_value is False, "<1% 占比不应丢弃"


def test_miss_ratio_over_1_percent_check_further(judge):
    """miss/context > 1% → 继续检查"""
    # miss=200, context=10000 → 2% > 1% → 往下走
    req = SubRequest(role="read", tokens=10000, cache_hit=9800, cache_miss=200, output_tokens=100)
    verdict = judge.assess([req], 10000)

    print(f"\n=== miss/context = 2% ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    # 继续检查 miss:hit 比例


# ========== 规则 2b: miss/hit < 1:15 → 保留 ==========

def test_miss_hit_ratio_under_1_15_keep(judge):
    """miss:hit < 1:15 → 不丢弃（几乎全命中）"""
    # miss=100, hit=2000 → 1:20 < 1:15 → 保留
    req = SubRequest(role="read", tokens=2100, cache_hit=2000, cache_miss=100, output_tokens=100)
    verdict = judge.assess([req], 5000)

    print(f"\n=== miss:hit = 1:20 (< 1:15) ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    assert verdict.is_low_value is False, "几乎全命中不应丢弃"


def test_miss_hit_ratio_over_1_15_discard(judge):
    """miss:hit > 1:15 → 可以丢弃"""
    # miss=300, hit=3000 → 1:10 > 1:15 → 丢弃
    req = SubRequest(role="read", tokens=3300, cache_hit=3000, cache_miss=300, output_tokens=100)
    verdict = judge.assess([req], 10000)

    print(f"\n=== miss:hit = 1:10 (> 1:15) ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    assert verdict.is_low_value is True, "miss:hit > 1:15 可以丢弃"


# ========== 典型场景集成测试 ==========

def test_typical_first_request_keep(judge):
    """第一次请求：0 hit，miss=500，context=1000 → 保留"""
    req = SubRequest(role="exploration", tokens=500, cache_hit=0, cache_miss=500, output_tokens=100)
    verdict = judge.assess([req], 1000)

    print(f"\n=== 第一次请求：0 hit, miss=500 ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    # hit=0 时 miss/hit = ∞ → 不满足 <1:15 → 不丢弃（正确！）
    assert verdict.is_low_value is False, "第一次请求 0 hit 不应丢弃！"


def test_high_hit_small_miss_discard(judge):
    """典型可丢弃场景：高命中，小 miss"""
    # miss=400, hit=8000, context=20000
    # miss ≤ 600 ✓
    # miss/context = 2% ≥ 1% ✓
    # miss/hit = 1:20 < 1:15 → 保留
    req = SubRequest(role="read", tokens=8400, cache_hit=8000, cache_miss=400, output_tokens=100)
    verdict = judge.assess([req], 20000)

    print(f"\n=== 高命中：miss=400, hit=8000 ===")
    print(f"  miss/context = 2%")
    print(f"  miss/hit = 1:20")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    # miss/hit=1:20 < 1:15 → 保留


def test_medium_hit_medium_miss_discard(judge):
    """中等命中 + 中等 miss → 可以丢弃"""
    # miss=500, hit=5000, context=15000
    # miss ≤ 600 ✓
    # miss/context ≈ 3.3% ≥ 1% ✓
    # miss/hit = 1:10 > 1:15 → 丢弃
    req = SubRequest(role="read", tokens=5500, cache_hit=5000, cache_miss=500, output_tokens=100)
    verdict = judge.assess([req], 15000)

    print(f"\n=== 中等命中：miss=500, hit=5000 ===")
    print(f"  miss/context ≈ 3.3%")
    print(f"  miss/hit = 1:10")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    assert verdict.is_low_value is True, "可以丢弃"


def test_almost_all_hit_keep(judge):
    """几乎全命中（95%+）→ 保留"""
    # miss=200, hit=5000, context=10000
    # miss/hit = 1:25 < 1:15 → 保留
    req = SubRequest(role="read", tokens=5200, cache_hit=5000, cache_miss=200, output_tokens=100)
    verdict = judge.assess([req], 10000)

    print(f"\n=== 几乎全命中：miss/hit = 1:25 ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")

    assert verdict.is_low_value is False


# ========== 边界值测试 ==========

def test_boundary_miss_600(judge):
    """边界：miss=600（刚好等于阈值）"""
    req = SubRequest(role="read", tokens=10000, cache_hit=9400, cache_miss=600, output_tokens=100)
    verdict = judge.assess([req], 20000)

    print(f"\n=== 边界：miss=600 ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")


def test_boundary_miss_context_1_percent(judge):
    """边界：miss/context = 1%"""
    req = SubRequest(role="read", tokens=200, cache_hit=100, cache_miss=100, output_tokens=50)
    verdict = judge.assess([req], 10000)  # 100/10000 = 1%

    print(f"\n=== 边界：miss/context = 1% ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")


def test_boundary_miss_hit_1_15(judge):
    """边界：miss/hit = 1:15"""
    req = SubRequest(role="read", tokens=1600, cache_hit=1500, cache_miss=100, output_tokens=50)
    verdict = judge.assess([req], 5000)  # 100/1500 = 1:15

    print(f"\n=== 边界：miss/hit = 1:15 ===")
    print(f"  is_low_value: {verdict.is_low_value}")
    print(f"  reason: {verdict.reason}")


def test_empty_task(judge):
    """空任务"""
    verdict = judge.assess([], 1000)
    assert verdict.is_low_value is False


# ========== 真实 multi-turn 场景 ==========

def test_multi_turn_all_hit_keep(judge):
    """多轮连续高命中 → 检查每一轮是否会被错误丢弃"""
    context = 3000
    for turn in range(1, 6):
        hit = 3000 - turn * 50  # 每轮增加 miss
        miss = turn * 50
        req = SubRequest(role="read", tokens=3000, cache_hit=hit, cache_miss=miss, output_tokens=100)
        verdict = judge.assess([req], context + turn * 200)
        print(f"  Turn {turn}: hit={hit}, miss={miss}, discard={verdict.is_low_value}")

    # Turn 4: miss=200 → 往下，miss/hit=1:14 → 丢弃
    # Turn 5: miss=250 → 往下，miss/hit=1:11 → 丢弃


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
