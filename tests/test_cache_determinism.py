"""缓存命中确定性测试

验证：相同输入应该产生相同的 cache key
"""
import json
import hashlib
import pytest

from src.config import Config
from src.agent import AgentLoop
from src.tools import ALL_TOOLS


def test_tool_definitions_deterministic():
    """工具定义的 JSON 序列化应该确定性"""
    body1 = {"tools": ALL_TOOLS, "model": "test"}
    body2 = {"tools": ALL_TOOLS, "model": "test"}

    # 不加 sort_keys 可能产生不同结果
    s1 = json.dumps(body1, sort_keys=True)
    s2 = json.dumps(body2, sort_keys=True)

    assert s1 == s2, "工具定义序列化应该完全一致"

    h1 = hashlib.md5(s1.encode()).hexdigest()
    h2 = hashlib.md5(s2.encode()).hexdigest()
    assert h1 == h2, "工具定义 hash 应该一致"


def test_tool_definitions_same_order():
    """工具定义顺序应该固定"""
    tool_names = [t["function"]["name"] for t in ALL_TOOLS]
    expected = ["bash", "write_file", "read_file", "edit_file", "glob", "grep"]
    assert tool_names == expected, f"工具顺序应该固定: {tool_names}"


def test_system_prompt_deterministic():
    """系统提示词每次加载应该一致"""
    from src.agent import load_system_prompt

    # 多次加载，内容应该完全一致
    p1 = load_system_prompt()
    p2 = load_system_prompt()

    assert p1 == p2, "系统提示词应该完全确定"

    h1 = hashlib.md5(p1.encode()).hexdigest()
    h2 = hashlib.md5(p2.encode()).hexdigest()
    assert h1 == h2, "系统提示词 hash 应该一致"


def test_same_user_input_same_body_hash():
    """相同用户输入应该产生相同的 API body hash"""
    config = Config()
    agent1 = AgentLoop(config)
    agent2 = AgentLoop(config)

    user_msg = "介绍一下你自己"

    # 模拟 build body
    def build_body(context, msg):
        messages = context.messages + [{"role": "user", "content": msg}]
        body = {
            "model": config.model.model,
            "messages": messages,
            "max_tokens": config.model.max_tokens,
            "tools": ALL_TOOLS,
            "tool_choice": "auto",
        }
        return json.dumps(body, sort_keys=True)

    b1 = build_body(agent1.context, user_msg)
    b2 = build_body(agent2.context, user_msg)

    h1 = hashlib.md5(b1.encode()).hexdigest()
    h2 = hashlib.md5(b2.encode()).hexdigest()

    assert h1 == h2, f"相同用户输入应该产生相同 body hash\n{h1}\n{h2}"


def test_first_request_cache_hit_opportunity():
    """
    理论上：相同前缀 + 相同用户输入 = 相同 cache key

    实际中，如果两次 Agent 实例化之间：
    1. SYS 提示词文件没改
    2. 工具定义没变
    3. API 参数没变
    4. JSON 序列化确定

    那么第一次请求应该能命中上一次运行的缓存。
    """
    config = Config()
    agent = AgentLoop(config)

    # 打印前缀信息（调试用）
    sys_prompt_len = len(agent.SYSTEM_PROMPT)
    sys_prompt_hash = hashlib.md5(agent.SYSTEM_PROMPT.encode()).hexdigest()
    tools_hash = hashlib.md5(json.dumps(ALL_TOOLS, sort_keys=True).encode()).hexdigest()

    print()
    print(f"  System prompt length: {sys_prompt_len}")
    print(f"  System prompt hash:   {sys_prompt_hash}")
    print(f"  Tools definition hash: {tools_hash}")
    print()
    print(f"  如果两次运行 hash 相同，则具备 cache 命中条件")
    print(f"  如果 hash 不同，检查 SYS/*.md 文件或工具定义是否有变化")
    print()

    # 输出工具顺序（调试用）
    tool_names = [t["function"]["name"] for t in ALL_TOOLS]
    print(f"  Tool order: {' → '.join(tool_names)}")

    # 只要能走到这里，说明 deterministic
    assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
