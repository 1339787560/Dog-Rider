"""
test_cache_ttl_bisect.py — 指数二分法测试 DeepSeek 缓存有效期

策略:
  1. 构建缓存 (2 次请求)
  2. 指数等待: 1m → 2m → 4m → 8m → 16m → 32m → 64m → 128m → ...
  3. 检测到 MISS 后，二分逼近精确 TTL
  4. 测试单一缓存 + 多缓存 + 不同上下文长度

用法:
  python Poc/test_cache_ttl_bisect.py              # 前台运行
  python Poc/test_cache_ttl_bisect.py --daemon     # 后台守护运行
  python Poc/test_cache_ttl_bisect.py --status     # 查看守护状态
  python Poc/test_cache_ttl_bisect.py --stop       # 停止守护进程

输出: 实时写入 Poc/cache_ttl_results.txt
PID:  守护模式下写入 Poc/cache_ttl_daemon.pid
"""

import json, os, sys, time, math, atexit, signal
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# ── 配置 ──────────────────────────────────────────

env = {}
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            if '=' in line:
                k, v = line.strip().split('=', 1)
                env[k] = v

API_KEY = env.get('DEEPSEEK_API_KEY') or os.environ.get('DEEPSEEK_API_KEY')
if not API_KEY:
    print("ERROR: DEEPSEEK_API_KEY not found in .env or environment")
    sys.exit(1)
MODEL = 'deepseek-v4-flash'
RESULTS_FILE = os.path.join(os.path.dirname(__file__), 'cache_ttl_results.txt')

# ── 上下文模板 ────────────────────────────────────

SHORT_CTX = (
    "You are a TypeScript expert for CP server development. "
    "You use async/await with modsvr.context, Redis distributed locks, and MySQL storage. "
    "You implement OnPayResult, OnClientRequest, OnInternalCall callbacks. "
    "You follow namespace conventions with Business, CommonFuncs, interf, TestTool."
)

MEDIUM_CTX = SHORT_CTX * 2 + (
    " You understand the leveldefine module which tracks player experience "
    "through tongbao consumption, calculates player grades from levelContent config, "
    "implements degradation when players are inactive for degradeDays, "
    "and manages oneOffRewardStatusArray for claim rewards. "
    "You are familiar with cmmonthcard, goldbank, convert modules."
)

LONG_CTX = MEDIUM_CTX * 2 + (
    " You follow the Doc-Writer skill conventions for documentation. "
    "You use CodeGraph for structural code analysis. "
    "You understand prefix caching and optimize context structure for cache hits. "
    "You are familiar with the full CP-DEV architecture including inter-module "
    "communication via async_internal_call, Redis key naming conventions, "
    "MySQL table structures, and the L0/L1/L2/L3 documentation hierarchy."
)

CONTEXTS = {
    "short": SHORT_CTX,
    "medium": MEDIUM_CTX,
    "long": LONG_CTX,
}

MULTI_PREFIXES = [
    "You are a TypeScript expert for CP server development. You use async/await, Redis, MySQL. You implement OnPayResult, OnClientRequest, OnInternalCall. You follow Business, CommonFuncs, interf, TestTool namespaces. You understand leveldefine, cmmonthcard, goldbank modules.",
    "You are a Python data scientist for ML pipelines. You use pandas, numpy, scikit-learn, PyTorch. You implement cross-validation, hyperparameter optimization, model deployment. You follow PEP 8 and write reproducible code with proper documentation.",
    "You are a DevOps engineer for cloud-native infrastructure. You manage Kubernetes, Terraform, CI/CD pipelines. You implement monitoring with Prometheus, Grafana. You design disaster recovery with proper RPO and RTO targets.",
]

PID_FILE = os.path.join(os.path.dirname(__file__), 'cache_ttl_daemon.pid')

# ── 守护进程 ──────────────────────────────────────

def daemonize():
    """后台守护化 (Windows 兼容)"""
    if sys.platform == 'win32':
        # Windows: 使用 pythonw.exe 启动无窗口子进程
        import subprocess
        script = os.path.abspath(__file__)
        pid = subprocess.Popen(
            [sys.executable, script, '--daemon-child'],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        ).pid
        print(f"Daemon started (PID={pid})")
        print(f"Monitor: tail -f Poc/cache_ttl_results.txt")
        print(f"Status:  python Poc/test_cache_ttl_bisect.py --status")
        print(f"Stop:    python Poc/test_cache_ttl_bisect.py --stop")
    else:
        # Unix: 标准 double-fork
        pid = os.fork()
        if pid > 0:
            print(f"Daemon started (PID={pid})")
            sys.exit(0)
        os.setsid()
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')
        daemon_child()

def daemon_child():
    """守护子进程入口"""
    global _daemon_mode
    _daemon_mode = True

    # 写入 PID
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    # 忽略 SIGHUP (终端关闭)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    # 注册退出清理
    def cleanup():
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    atexit.register(cleanup)

    # 运行主流程
    log("Daemon started")
    try:
        main_work()
    except Exception as e:
        log(f"Fatal: {e}")
    finally:
        cleanup()

def daemon_status():
    """查看守护状态"""
    if not os.path.exists(PID_FILE):
        print("No daemon running")
        return
    with open(PID_FILE) as f:
        pid = int(f.read().strip())
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
    if handle:
        kernel32.CloseHandle(handle)
        print(f"Daemon running (PID={pid})")
        # 显示最近结果
        logfile = os.path.join(os.path.dirname(__file__), 'cache_ttl_results.txt')
        if os.path.exists(logfile):
            with open(logfile, encoding='utf-8') as f:
                lines = f.readlines()
                print(f"Recent ({min(5, len(lines))} lines):")
                for line in lines[-5:]:
                    print(f"  {line.rstrip()}")
    else:
        print(f"PID file found but process not running (PID={pid})")
        os.remove(PID_FILE)

def daemon_stop():
    """停止守护进程"""
    if not os.path.exists(PID_FILE):
        print("No daemon running")
        return
    with open(PID_FILE) as f:
        pid = int(f.read().strip())
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if handle:
        kernel32.TerminateProcess(handle, 0)
        kernel32.CloseHandle(handle)
        print(f"Daemon stopped (PID={pid})")
    else:
        print(f"Process not found (PID={pid})")
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

# ── API 调用 ──────────────────────────────────────

def call(system, user_msg):
    body = {
        'model': MODEL,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg}
        ],
        'max_tokens': 20
    }
    data = json.dumps(body).encode()
    url = 'https://api.deepseek.com/chat/completions'
    req = Request(url, data=data, headers={
        'Authorization': 'Bearer ' + API_KEY,
        'Content-Type': 'application/json',
    })
    try:
        resp = urlopen(req, timeout=30)
        result = json.loads(resp.read())
        u = result['usage']
        return {
            'prompt': u['prompt_tokens'],
            'hit': u.get('prompt_cache_hit_tokens', 0),
            'miss': u.get('prompt_cache_miss_tokens', u['prompt_tokens']),
        }
    except HTTPError as e:
        log(f"  API Error: {e.code} {e.read().decode()[:200]}")
        return None
    except Exception as e:
        log(f"  Error: {e}")
        return None

_daemon_mode = False

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    # 始终写文件
    with open(RESULTS_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')
    # 非守护模式才打印到控制台
    if not _daemon_mode:
        print(line, flush=True)

def warmup(system, n=2):
    """预热: 发 n 次请求构建缓存"""
    for i in range(n):
        call(system, f'warmup {i}')
    time.sleep(2)
    # 验证预热成功
    u = call(system, 'verify warmup')
    if u and u['hit'] > 0:
        return True
    return False

def check_cache(system):
    """检查缓存是否命中"""
    u = call(system, f'cache check {time.time():.0f}')
    if u is None:
        return None, None
    return u['hit'], u['miss']

# ── 指数二分测试 ──────────────────────────────────

def test_ttl(system, label, max_wait_min=256):
    """指数等待 + 二分逼近 TTL"""
    log(f"\n{'='*50}")
    log(f"测试: {label}")
    log(f"{'='*50}")

    # 预热
    log(f"  预热中...")
    if not warmup(system):
        log(f"  预热失败，跳过")
        return None
    log(f"  预热成功")

    # 指数等待
    wait_min = 1
    last_hit_time = time.time()
    last_hit_wait = 0

    while wait_min <= max_wait_min:
        log(f"  等待 {wait_min} 分钟...")
        # 心跳: 每 30s 打一次进度
        slept = 0
        while slept < wait_min * 60:
            time.sleep(min(30, wait_min * 60 - slept))
            slept += 30
            log(f"    ... {slept//60}m/{wait_min}m")

        hit, miss = check_cache(system)
        if hit is None:
            log(f"  API 错误，重试...")
            time.sleep(10)
            hit, miss = check_cache(system)

        elapsed = (time.time() - last_hit_time) / 60
        if hit is not None and hit > 0:
            log(f"  HIT (hit={hit} miss={miss}) elapsed={elapsed:.1f}min")
            last_hit_time = time.time()
            last_hit_wait = wait_min
            wait_min *= 2  # 指数增长
        else:
            log(f"  MISS (hit={hit} miss={miss}) elapsed={elapsed:.1f}min")
            # 二分逼近
            ttl = bisect_ttl(system, last_hit_wait, wait_min, last_hit_time)
            log(f"\n  >>> {label} TTL ≈ {ttl:.1f} 分钟 ({ttl/60:.2f} 小时)")
            return ttl

    log(f"  {max_wait_min} 分钟内未过期，TTL > {max_wait_min} 分钟")
    return max_wait_min

def bisect_ttl(system, low_min, high_min, base_time):
    """二分逼近精确 TTL"""
    log(f"  二分逼近: [{low_min}min, {high_min}min]")

    for _ in range(10):  # 最多 10 次二分
        mid_min = (low_min + high_min) / 2
        if (high_min - low_min) < 1:
            break

        # 计算还需要等多久才能到达 mid_min 时刻（相对 base_time）
        target_time = base_time + mid_min * 60
        delta = target_time - time.time()
        if delta > 0:
            log(f"    等待 {delta/60:.1f} 分钟到达 T+{mid_min:.0f}min ...")
            # 心跳: 每 30s 打进度
            while delta > 0:
                chunk = min(30, delta)
                time.sleep(chunk)
                delta -= chunk
                log(f"      ... 还剩 {delta/60:.1f}min")

        elapsed_since_base = (time.time() - base_time) / 60
        hit, miss = check_cache(system)

        if hit is not None and hit > 0:
            log(f"    HIT (elapsed={elapsed_since_base:.1f}min) -> TTL > {elapsed_since_base:.1f}min")
            low_min = elapsed_since_base
        else:
            log(f"    MISS (elapsed={elapsed_since_base:.1f}min) -> TTL < {elapsed_since_base:.1f}min")
            high_min = elapsed_since_base

    result = (low_min + high_min) / 2
    log(f"  二分结果: TTL ≈ {result:.1f} 分钟")
    return result

# ── 主流程 ──────────────────────────────────────────

def main_work():
    # 初始化结果文件
    with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
        f.write(f"DeepSeek Cache TTL Test - {datetime.now()}\n")
        f.write(f"Model: {MODEL}\n")
        f.write(f"{'='*60}\n\n")

    results = {}

    # ── 测试 1: 单一缓存，不同上下文长度 ──
    log("\n" + "="*60)
    log("Phase 1: 单一缓存 TTL vs 上下文长度")
    log("="*60)

    for ctx_label, system in CONTEXTS.items():
        ttl = test_ttl(system, f"single-{ctx_label}", max_wait_min=128)
        results[f"single-{ctx_label}"] = ttl

    # ── 测试 2: 多缓存同时存活 ──
    log("\n" + "="*60)
    log("Phase 2: 多缓存 TTL (3 个不同前缀)")
    log("="*60)

    # 同时预热 3 个前缀
    log("  预热 3 个前缀...")
    for i, prefix in enumerate(MULTI_PREFIXES):
        warmup(prefix, n=2)
        log(f"  前缀 {i+1} 预热完成")

    # 同时检查 3 个前缀的缓存
    def check_all_multi():
        all_hit = True
        for i, prefix in enumerate(MULTI_PREFIXES):
            hit, miss = check_cache(prefix)
            if hit is None or hit == 0:
                all_hit = False
                log(f"    前缀 {i+1}: MISS")
            else:
                log(f"    前缀 {i+1}: HIT (hit={hit})")
        return all_hit

    # 指数等待
    wait_min = 1
    start_time = time.time()

    while wait_min <= 256:
        log(f"  等待 {wait_min} 分钟...")
        slept = 0
        while slept < wait_min * 60:
            time.sleep(min(30, wait_min * 60 - slept))
            slept += 30
            log(f"    ... {slept//60}m/{wait_min}m")

        all_hit = check_all_multi()
        elapsed = (time.time() - start_time) / 60

        if all_hit:
            log(f"  全部 HIT (elapsed={elapsed:.1f}min)")
            wait_min *= 2
        else:
            log(f"  部分 MISS (elapsed={elapsed:.1f}min)")
            results["multi-prefix"] = elapsed
            break
    else:
        results["multi-prefix"] = 256

    # ── 汇总 ──
    log("\n" + "="*60)
    log("汇总结果")
    log("="*60)

    with open(RESULTS_FILE, 'a', encoding='utf-8') as f:
        f.write(f"\n{'='*60}\n")
        f.write("汇总结果\n")
        f.write(f"{'='*60}\n")
        for label, ttl in results.items():
            line = f"  {label}: TTL ≈ {ttl:.1f} min ({ttl/60:.2f} hours)"
            log(line)
            f.write(line + '\n')

    log(f"\n完整结果已写入: {RESULTS_FILE}")
    log("Daemon finished")

if __name__ == '__main__':
    if '--status' in sys.argv:
        daemon_status()
    elif '--stop' in sys.argv:
        daemon_stop()
    elif '--daemon' in sys.argv:
        daemonize()
    elif '--daemon-child' in sys.argv:
        daemon_child()
    else:
        main_work()
