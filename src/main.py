"""Dog-Rider Agent 入口 - 任务级丢弃策略验证"""
import argparse
import sys

# Windows GBK 编码兼容
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .agent import AgentLoop
from .commands import CommandRegistry
from .config import load_env_config
from .scenarios import run_scenario, run_all_scenarios, SCENARIOS


def main():
    parser = argparse.ArgumentParser(description="Dog-Rider - Task-level discard strategy agent")
    parser.add_argument("--scenario", type=str, help="Run preset scenario: " + ", ".join(SCENARIOS.keys()))
    parser.add_argument("--all-scenarios", action="store_true", help="Run all preset scenarios")
    parser.add_argument("--no-auto-discard", action="store_true", help="Don't auto-discard, just print verdict")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--model", type=str, help="Override model name")
    args = parser.parse_args()

    config = load_env_config()

    if not config.model.api_key:
        print("Error: DEEPSEEK_API_KEY not set.")
        print("Please set it in .env file or environment variable.")
        sys.exit(1)

    if args.no_auto_discard:
        config.discard.auto_discard = False
    if args.verbose:
        config.verbose = True
    if args.model:
        config.model.model = args.model

    if args.all_scenarios:
        success = run_all_scenarios(config)
        sys.exit(0 if success else 1)

    if args.scenario:
        success = run_scenario(args.scenario, config)
        sys.exit(0 if success else 1)

    # 交互模式
    print(f"Dog-Rider Agent v0.1.0 | model={config.model.model}")
    print(f"Auto discard: {'ON' if config.discard.auto_discard else 'OFF'}")
    print(f"Type /help for commands.")
    print()

    agent = AgentLoop(config)
    commands = CommandRegistry()

    print(f"Session: {agent.session_id}  (use /resume <id> to resume an old session)")
    print()

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            agent.print_stats()
            break

        if not user_input:
            continue

        # ── Slash 命令分发 ──
        if user_input.startswith("/"):
            result = commands.dispatch(user_input, agent)
            if result.output:
                print(result.output)
            if not result.should_continue:
                break
            continue

        try:
            response = agent.run_task(user_input)
            print(f"\n{response}")
        except Exception as e:
            print(f"\nError: {e}")
            import traceback
            if config.verbose:
                traceback.print_exc()


if __name__ == "__main__":
    main()
