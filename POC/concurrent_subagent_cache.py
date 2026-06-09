"""
concurrent_subagent_cache.py — 验证并发 subagent 的缓存命中策略

测试三种模式：
1. 无预热并发：全部 miss
2. 预热后并发：shared prefix 命中
3. 分波并发：每波共享前一层缓存

结论写入 Poc/deepseek.md
"""

import json, os, sys, time
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed

env = {}
with open(os.path.join(os.path.dirname(__file__), '..', '.env')) as f:
    for line in f:
        if '=' in line:
            k, v = line.strip().split('=', 1)
            env[k] = v

key = env['DEEPSEEK_API_KEY']
model = 'deepseek-v4-flash'

# 模拟 Dog-Rider 的 shared prefix
SHARED_PREFIX = """You are a specialized AI agent for code reading and writing in the Dog-Rider project. Your role is to analyze TypeScript code in CP server modules, understand their architecture, and produce documentation or code modifications. You follow the CP-DEV type definition with sections for module overview, runtime flow, client request interfaces, inter-module communication, data structures, and utility classes. You use async/await patterns, Redis distributed locks, and MySQL storage. You implement CP service callbacks including OnPayResult, OnClientRequest, OnInternalCall, and OnScriptReload. You follow namespace conventions with Business for core logic, CommonFuncs for utilities, interf for data structures, and TestTool for testing. You understand prefix caching and optimize your context structure for cache hits. You keep stable content as prefixes and variable content as suffixes. You follow the Doc-Writer skill conventions for documentation. You use CodeGraph for structural code analysis. You understand the leveldefine module which tracks player experience through tongbao consumption."""

# 模拟不同的任务上下文
TASK_CONTEXTS = [
    "File: leveldefine_xzmp.ts\nnamespace Business {\n  export async function async_QueryPlayerLevelInfo(cxt, userid) { ... }\n  export async function async_WritePlayerLevelInfo(cxt, userid, data) { ... }\n}",
    "File: cmmonthcard_xzmp.ts\nnamespace Business {\n  export async function async_queryMonthCardInfo(cxt, userid) { ... }\n  export async function async_buyMonthCard(cxt, userid, cardType) { ... }\n}",
    "File: cmnewplayerdailygift_xzmp.ts\nnamespace Business {\n  export async function async_queryDailyGiftInfo(cxt, userid) { ... }\n  export async function async_claimDailyGift(cxt, userid, giftId) { ... }\n}",
    "File: goldbank_xzmp.ts\nnamespace Business {\n  export async function async_queryGoldBankInfo(cxt, userid) { ... }\n  export async function async_depositGold(cxt, userid, amount) { ... }\n}",
    "File: convert_xzmp.ts\nnamespace Business {\n  export async function async_queryMigrationData(cxt, userid) { ... }\n  export async function async_executeMigration(cxt, userid, data) { ... }\n}",
]

TASK_INSTRUCTIONS = [
    "Analyze this module's data flow and write an interface document.",
    "Identify the caching strategy and suggest optimizations.",
    "Review the error handling patterns and propose improvements.",
    "Document the inter-module communication protocols.",
    "Explain the degradation mechanism and recovery logic.",
]

def call(system, user_msg):
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg}
        ],
        'max_tokens': 50
    }
    data = json.dumps(body).encode()
    req = Request('https://api.deepseek.com/chat/completions', data=data, headers={
        'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json',
    })
    resp = urlopen(req, timeout=60)
    result = json.loads(resp.read())
    u = result['usage']
    return {
        'prompt': u['prompt_tokens'],
        'hit': u.get('prompt_cache_hit_tokens', 0),
        'miss': u.get('prompt_cache_miss_tokens', u['prompt_tokens']),
        'out': u['completion_tokens'],
    }

def concurrent_calls(n, system, tasks):
    """并发发起 n 个请求"""
    results = []
    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = {executor.submit(call, system, task): i for i, task in enumerate(tasks[:n])}
        for future in as_completed(futures):
            results.append(future.result())
    return results

def print_results(label, results):
    total_hit = sum(r['hit'] for r in results)
    total_miss = sum(r['miss'] for r in results)
    total_prompt = sum(r['prompt'] for r in results)
    hit_rate = total_hit / total_prompt * 100 if total_prompt else 0
    cost = (total_miss * 0.14 + total_hit * 0.0028 + sum(r['out'] for r in results) * 0.28) / 1_000_000
    print(f'\n  {label}:')
    print(f'    requests={len(results)} prompt={total_prompt} hit={total_hit} miss={total_miss} hit_rate={hit_rate:.1f}%')
    print(f'    cost=${cost:.6f}')
    for i, r in enumerate(results):
        print(f'      [{i+1}] prompt={r["prompt"]:3d} hit={r["hit"]:3d} miss={r["miss"]:3d}')

# ── 测试 ──────────────────────────────────────────

print('=== 模式 1: 无预热并发 (5 个同时请求) ===')
tasks = [f'{ctx}\n\n{inst}' for ctx, inst in zip(TASK_CONTEXTS, TASK_INSTRUCTIONS)]
r1 = concurrent_calls(5, SHARED_PREFIX, tasks)
print_results('无预热', r1)

time.sleep(3)

print('\n=== 模式 2: 预热后并发 ===')
# 预热: 发 1 个请求构建 shared prefix 缓存
print('  预热中...')
warmup = call(SHARED_PREFIX, 'warmup')
print(f'  预热完成: prompt={warmup["prompt"]} miss={warmup["miss"]}')
time.sleep(2)

# 并发 5 个请求
r2 = concurrent_calls(5, SHARED_PREFIX, tasks)
print_results('预热后并发', r2)

time.sleep(3)

print('\n=== 模式 3: 分波并发 (2 波, 每波 3 个) ===')
# Wave 1
print('  Wave 1 发起...')
r3a = concurrent_calls(3, SHARED_PREFIX, tasks[:3])
print_results('Wave 1', r3a)

time.sleep(2)

# Wave 2
print('  Wave 2 发起...')
r3b = concurrent_calls(3, SHARED_PREFIX, tasks[3:6])
print_results('Wave 2', r3b)

# 总结
print('\n=== 总结 ===')
print(f'  模式 1 (无预热): hit_rate={sum(r["hit"] for r in r1)/sum(r["prompt"] for r in r1)*100:.1f}%')
print(f'  模式 2 (预热后): hit_rate={sum(r["hit"] for r in r2)/sum(r["prompt"] for r in r2)*100:.1f}%')
print(f'  模式 3 (分波):   Wave1={sum(r["hit"] for r in r3a)/sum(r["prompt"] for r in r3a)*100:.1f}% Wave2={sum(r["hit"] for r in r3b)/sum(r["prompt"] for r in r3b)*100:.1f}%')
