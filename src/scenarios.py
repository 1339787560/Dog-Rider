"""预设测试场景 - 用于验证丢弃策略效果"""
import sys
from typing import Callable, Dict, List

from .agent import AgentLoop
from .config import Config


class Scenario:
    """测试场景"""

    def __init__(self, name: str, description: str, user_inputs: List[str], expected_outcome: str):
        self.name = name
        self.description = description
        self.user_inputs = user_inputs
        self.expected_outcome = expected_outcome  # "discard" / "keep" / "partial"


# 预设场景
SCENARIOS: Dict[str, Scenario] = {
    "explore": Scenario(
        name="explore",
        description="探索任务 - 大量 grep/read，无输出/写入 → 应丢弃",
        user_inputs=[
            "List all Python files in Poc directory and show their names.",
            "Now read the contents of Poc/tokenizer_poc.py, just the first 200 lines.",
            "Search for all occurrences of 'cache_hit' in Poc/tokenizer_poc.py.",
            "Tell me what classes are defined in this file.",
        ],
        expected_outcome="discard",
    ),
    "analyze": Scenario(
        name="analyze",
        description="深度分析任务 - 多轮思考分析 → 应保留",
        user_inputs=[
            "I want you to analyze our tokenizer-based task discard strategy.",
            "First, explain what the 5 dimensions of scoring are: cache contribution, context ratio, output density, chain depth, role mix.",
            "Then explain why pure exploration tasks (grep/ls) get low scores.",
            "Finally, suggest 3 ways to improve the scoring accuracy for edge cases.",
        ],
        expected_outcome="keep",
    ),
    "write": Scenario(
        name="write",
        description="写入任务 - 生成代码 → 应保留",
        user_inputs=[
            "Create a simple Python file at Poc/test_discard.py that prints 'hello world'.",
            "Now modify it to also print the current timestamp when run.",
            "Add a function that counts from 1 to 10 with 0.1 second delay between each.",
        ],
        expected_outcome="keep",
    ),
    "mixed": Scenario(
        name="mixed",
        description="混合任务 - 先探索再分析 → 可疑 (score 0.35-0.65)",
        user_inputs=[
            "List all .md files in Poc/.",
            "Read the first 100 lines of Poc/deepseek.md.",
            "Based on what you read, summarize the cache mechanism of DeepSeek API.",
            "Then explain how the 128-token granularity affects cache hit rate.",
        ],
        expected_outcome="partial",
    ),
}


def run_scenario(name: str, config: Config, agent: AgentLoop = None) -> bool:
    """运行指定场景

    Returns: True if scenario completed successfully
    """
    if name not in SCENARIOS:
        print(f"Unknown scenario: {name}")
        print(f"Available: {', '.join(SCENARIOS.keys())}")
        return False

    scenario = SCENARIOS[name]
    print(f"\n{'#' * 60}")
    print(f"SCENARIO: {scenario.name}")
    print(f"Description: {scenario.description}")
    print(f"Expected: {scenario.expected_outcome}")
    print(f"{'#' * 60}")

    agent = agent or AgentLoop(config)

    print(f"\nInitial context state:")
    print(agent.context.report())

    for i, user_input in enumerate(scenario.user_inputs, 1):
        print(f"\n{'─' * 40}")
        print(f"Turn {i}/{len(scenario.user_inputs)}: {user_input[:60]}...")
        print(f"{'─' * 40}")

        try:
            response = agent.run_task(user_input)
            print(f"\nResponse: {response[:200]}..." if len(response) > 200 else f"\nResponse: {response}")
        except Exception as e:
            print(f"\nError: {e}")
            import traceback
            traceback.print_exc()
            return False

    print(f"\n{'#' * 60}")
    print("FINAL STATE")
    print(f"{'#' * 60}")
    print(agent.context.report())
    agent.print_stats()

    # 判断结果是否符合预期
    verdict = agent.last_verdict
    if verdict:
        actual = "discard" if verdict.is_low_value else "keep"
        match = actual == scenario.expected_outcome or scenario.expected_outcome == "partial"
        print(f"\nResult: {actual.upper()} (expected: {scenario.expected_outcome}) {'✓' if match else '✗'}")
        return match
    else:
        print("\nNo verdict recorded")
        return False


def run_all_scenarios(config: Config) -> bool:
    """运行所有场景"""
    print(f"\nRunning all {len(SCENARIOS)} scenarios...")
    results = {}
    agent = AgentLoop(config)

    for name in SCENARIOS:
        try:
            results[name] = run_scenario(name, config, agent)
        except Exception as e:
            print(f"\nScenario {name} failed with error: {e}")
            results[name] = False

    print(f"\n{'#' * 60}")
    print("SCENARIO SUMMARY")
    print(f"{'#' * 60}")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        print(f"  {name:12s}: {'✓ PASS' if ok else '✗ FAIL'}")
    print(f"\nTotal: {passed}/{total} passed")

    return passed == total
