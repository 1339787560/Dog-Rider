"""15 线程并发测试 — 每个线程独立 Agent，不共享 context/stats

验证: 各线程命中率独立稳定，接近 97.8%
"""
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import AgentLoop
from src.config import Config, load_env_config


@dataclass
class ThreadResult:
    thread_id: int
    success: bool = False
    hit: int = 0
    miss: int = 0
    prompts: int = 0
    turns: int = 0
    kept: bool = False
    error: str = ""
    response_len: int = 0


def run_thread(config: Config, system_prompt_messages: list, prompt: str,
              results: list, lock: threading.Lock):
    """每个线程独立创建 Agent，完全独立统计"""
    tid = threading.get_ident()
    result = ThreadResult(thread_id=tid)

    agent = AgentLoop(config)
    agent.silent = True
    # 复制预热好的上下文（深拷贝消息内容）
    agent.context.messages = [dict(m) for m in system_prompt_messages]

    try:
        response = agent.run_task(prompt)
        result.success = True
        result.response_len = len(response)
        result.kept = not agent.last_verdict.is_low_value if agent.last_verdict else False
        # 直接读独立 stat（无需减基数）
        result.hit = agent.stats.total_hit
        result.miss = agent.stats.total_miss
        result.prompts = agent.stats.total_prompt_tokens
        result.turns = agent.stats.requests
    except Exception as e:
        result.error = str(e)[:200]
        result.success = False

    with lock:
        results.append(result)


def main():
    print("=" * 70)
    print("多线程并发测试 — 每线程独立 Agent，独立统计")
    print("=" * 70)

    config = load_env_config()
    config.discard.merge_mode = "parallel"
    config.discard.auto_discard = True
    config.verbose = False

    # 预热一次，建立缓存基线
    print("Step 1: Warmup — 预热建立缓存基线")
    warmup_agent = AgentLoop(config)
    warmup_agent.silent = True
    warmup_agent.run_task("介绍一下你自己")
    baseline_messages = warmup_agent.context.messages
    print(f"Warmup done, baseline messages: {len(baseline_messages)}")
    print()

    # 并发 N 线程（每线程独立 Agent）
    thread_count = config.test.thread_count
    print(f"Step 2: 并发 {thread_count} 线程执行相同任务... ", end="", flush=True)
    results = []
    results_lock = threading.Lock()
    threads = []

    for i in range(thread_count):
        t = threading.Thread(
            target=run_thread,
            args=(config, baseline_messages, "介绍一下你自己", results, results_lock),
            name=f"worker-{i:02d}"
        )
        threads.append(t)

    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start
    print(f"done in {elapsed:.2f}s")
    print()

    # 汇总结果
    print("=" * 70)
    print("Results Summary")
    print("=" * 70)

    success = [r for r in results if r.success]
    errors = [r for r in results if not r.success]
    kept = [r for r in success if r.kept]
    discarded = [r for r in success if not r.kept]

    print(f"  Total tasks:  {len(results)}")
    print(f"  Success:      {len(success)}")
    print(f"  Errors:       {len(errors)}")
    print(f"  KEEP:         {len(kept)}")
    print(f"  DISCARD:      {len(discarded)}")

    if errors:
        print()
        print("  Errors:")
        for e in errors[:5]:
            print(f"    {e.thread_id}: {e.error}")

    # 各线程命中率（独立统计，互不干扰）
    print()
    print("  Per-thread hit rate:")
    hit_rates = []
    for i, r in enumerate(sorted(success, key=lambda x: x.turns), 1):
        if r.prompts > 0:
            rate = r.hit / r.prompts * 100
            hit_rates.append(rate)
            print(f"    Thread {i:2d}: hit={r.hit:5d}, miss={r.miss:4d}, {rate:5.1f}% ({r.turns} turn(s))")

    if hit_rates:
        avg_rate = sum(hit_rates) / len(hit_rates)
        min_rate = min(hit_rates)
        max_rate = max(hit_rates)
        print()
        print(f"    Avg: {avg_rate:.1f}%, Min: {min_rate:.1f}%, Max: {max_rate:.1f}%")
        print(f"    Range: {max_rate - min_rate:.1f}%")

    # 总计 token 用量
    total_hit = sum(r.hit for r in success)
    total_miss = sum(r.miss for r in success)
    total_prompts = sum(r.prompts for r in success)
    total_turns = sum(r.turns for r in success)
    total_rate = total_hit / total_prompts * 100 if total_prompts > 0 else 0

    print()
    print("=" * 70)
    print("总计 (Total across all threads)")
    print("=" * 70)
    print(f"  Total turns:    {total_turns}")
    print(f"  Total prompt:   {total_prompts}")
    print(f"  Total hit:      {total_hit}")
    print(f"  Total miss:     {total_miss}")
    print(f"  Overall rate:   {total_rate:.1f}%")


if __name__ == "__main__":
    main()
