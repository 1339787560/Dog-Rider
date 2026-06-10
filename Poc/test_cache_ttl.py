"""
test_cache_ttl.py — 测试缓存有效时间与上下文长度的关系

策略：
1. 用不同长度的 system prompt 构建缓存
2. 等待不同时间间隔后重新请求
3. 检查缓存是否仍然命中

测试矩阵：
  上下文长度: ~150 token, ~500 token, ~1000 token
  等待时间:   10s, 30s, 60s, 120s
"""

import json, os, sys, time
from urllib.request import Request, urlopen

env = {}
with open(os.path.join(os.path.dirname(__file__), '..', '.env')) as f:
    for line in f:
        if '=' in line:
            k, v = line.strip().split('=', 1)
            env[k] = v

key = env['DEEPSEEK_API_KEY']
model = 'deepseek-v4-flash'

# 三种长度的 system prompt
SHORT = "You are a TypeScript expert for CP server development. You use async/await with modsvr.context, Redis distributed locks, and MySQL storage. You implement OnPayResult, OnClientRequest, OnInternalCall callbacks."

MEDIUM = SHORT * 3 + " You follow namespace conventions with Business for core logic, CommonFuncs for utilities, interf for data structures, and TestTool for testing. You understand the leveldefine module which tracks player experience through tongbao consumption, calculates player grades from levelContent config, implements degradation when players are inactive for degradeDays, and manages oneOffRewardStatusArray for claim rewards. You understand prefix caching and optimize your context structure for cache hits."

LONG = MEDIUM * 2 + " You are also familiar with the cmmonthcard module for monthly card subscriptions, the goldbank module for gold storage with save limits, the convert module for data migration between systems, and the cmnewplayerdailygift module for daily gift distribution. Each module follows the same CP-DEV architecture with callbacks, inter-module communication via async_internal_call, and Redis+MySQL dual storage. You understand the Doc-Writer skill conventions for writing structured documentation with YAML headers, content trees, and type-specific formatting rules."

PROMPTS = {
    "short (~150 tok)": SHORT,
    "medium (~500 tok)": MEDIUM,
    "long (~1000 tok)": LONG,
}

WAIT_TIMES = [10, 30, 60, 120]  # seconds

def call(system, user_msg):
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg}
        ],
        'max_tokens': 20
    }
    data = json.dumps(body).encode()
    req = Request('https://api.deepseek.com/chat/completions', data=data, headers={
        'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json',
    })
    resp = urlopen(req, timeout=30)
    result = json.loads(resp.read())
    u = result['usage']
    return {
        'prompt': u['prompt_tokens'],
        'hit': u.get('prompt_cache_hit_tokens', 0),
        'miss': u.get('prompt_cache_miss_tokens', u['prompt_tokens']),
    }

def warmup(system):
    """发 2 次请求构建缓存"""
    call(system, 'warmup1')
    call(system, 'warmup2')

def check_cache(system, label):
    """检查缓存是否命中"""
    u = call(system, f'cache check at {time.time():.0f}')
    hit_rate = u['hit'] / u['prompt'] * 100 if u['prompt'] else 0
    return u['hit'], u['miss'], hit_rate

# ── 测试 ──────────────────────────────────────────

print(f"模型: {model}")
print(f"等待时间: {WAIT_TIMES}")
print()

results = {}

for prompt_label, system in PROMPTS.items():
    print(f"=== {prompt_label} ===")

    # 预热
    print(f"  预热中...")
    warmup(system)
    time.sleep(2)

    # 验证预热成功
    hit, miss, rate = check_cache(system, "warmup verify")
    print(f"  预热验证: hit={hit} miss={miss} rate={rate:.1f}%")

    if hit == 0:
        print(f"  预热失败，跳过")
        continue

    results[prompt_label] = {}

    # 逐个等待时间测试
    for wait in WAIT_TIMES:
        print(f"  等待 {wait}s...", end="", flush=True)
        time.sleep(wait)

        hit, miss, rate = check_cache(system, f"{wait}s")
        results[prompt_label][wait] = {
            'hit': hit, 'miss': miss, 'rate': rate
        }

        status = "HIT" if hit > 0 else "MISS"
        print(f" hit={hit} miss={miss} rate={rate:.1f}% [{status}]")

    print()

# ── 汇总 ──────────────────────────────────────────

print("=" * 60)
print("汇总结果")
print("=" * 60)
print(f"{'上下文长度':<20}", end="")
for wait in WAIT_TIMES:
    print(f"{'等待'+str(wait)+'s':<12}", end="")
print()
print("-" * 60)

for prompt_label in PROMPTS:
    if prompt_label not in results:
        continue
    print(f"{prompt_label:<20}", end="")
    for wait in WAIT_TIMES:
        if wait in results[prompt_label]:
            r = results[prompt_label][wait]
            status = f"{r['rate']:.0f}%" if r['hit'] > 0 else "MISS"
            print(f"{status:<12}", end="")
        else:
            print(f"{'N/A':<12}", end="")
    print()

print()
print("结论: 缓存命中率随等待时间的变化趋势")
