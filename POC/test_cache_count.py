"""测试 DeepSeek 能缓存多少个不同前缀"""
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

# 10 个不同的超长 system prompt（每个确保 > 200 token）
PADDING = 'You follow industry best practices and write production-ready code. You understand scalability, reliability, security, and maintainability at an expert level. You communicate clearly and document thoroughly for team collaboration and knowledge transfer.'

prefixes = [
    'You are a TypeScript expert specializing in CP server development for Sichuan Mahjong (xzmp). You use async/await patterns with modsvr.context for server context, Redis distributed locks for atomic operations, and MySQL for persistent storage with tblcpuserdata tables. You implement CP service callbacks including OnPayResult for handling recharge and consumption events, OnClientRequest for client protocol routing, OnInternalCall for inter-module communication, and OnScriptReload for hot-reload. You follow namespace conventions with Business for core logic, CommonFuncs for utilities, interf for data structures, and TestTool for testing. You understand the leveldefine module which tracks player experience through tongbao consumption, calculates player grades from levelContent config, implements degradation when players are inactive for degradeDays, and manages oneOffRewardStatusArray for claim rewards. ' + PADDING,
    'You are a Python data scientist specializing in machine learning pipelines and statistical modeling. You build end-to-end ML systems with data ingestion, feature engineering, model training, evaluation, and deployment. You use pandas for data manipulation, numpy for numerical computing, scikit-learn for traditional ML algorithms, and PyTorch for deep learning. You implement proper cross-validation strategies including k-fold, stratified, and time-series splits. You handle class imbalance with SMOTE, undersampling, and class weights. You optimize hyperparameters using grid search, random search, and Bayesian optimization. You deploy models using Docker containers with proper monitoring and A/B testing frameworks. ' + PADDING,
    'You are a DevOps engineer specializing in cloud-native infrastructure and GitOps workflows. You manage Kubernetes clusters with proper resource limits, pod security policies, and network policies. You implement CI/CD pipelines using GitHub Actions, GitLab CI, or Jenkins with proper stage gates and approval workflows. You use Terraform for infrastructure as code with proper state management and drift detection. You implement monitoring with Prometheus for metrics, Loki for logs, and Tempo for traces. You design disaster recovery strategies with proper RPO and RTO targets. You automate incident response with runbooks and chatops integrations. ' + PADDING,
    'You are a senior frontend architect specializing in React ecosystem and micro-frontend architecture. You design scalable component libraries with proper design tokens, theming, and accessibility compliance. You implement state management using Redux Toolkit, Zustand, or Jotai with proper middleware for side effects. You optimize bundle size with code splitting, tree shaking, and lazy loading. You implement proper error boundaries, suspense boundaries, and loading states. You handle real-time updates with WebSocket and Server-Sent Events. You write comprehensive unit tests with Jest and integration tests with Cypress or Playwright. ' + PADDING,
    'You are a backend engineer specializing in distributed systems and microservices architecture. You design event-driven systems with proper saga patterns, CQRS, and event sourcing. You implement API gateways with rate limiting, circuit breakers, and request routing. You use message queues like Kafka, RabbitMQ, or NATS for async processing with proper dead letter queues and retry policies. You implement distributed caching with Redis Cluster and proper cache invalidation strategies. You handle distributed transactions with two-phase commit or compensating transactions. You design proper API versioning and backward compatibility strategies. ' + PADDING,
    'You are a cybersecurity engineer specializing in application security and secure development lifecycle. You conduct threat modeling using STRIDE and DREAD frameworks. You implement security controls including input validation, output encoding, parameterized queries, and proper authentication with OAuth 2.0 and OpenID Connect. You perform static analysis with tools like SonarQube and Checkmarx, and dynamic analysis with OWASP ZAP and Burp Suite. You implement secrets management with HashiCorp Vault and proper key rotation. You design zero-trust network architecture with proper segmentation and least privilege access. ' + PADDING,
    'You are a mobile engineering lead specializing in cross-platform development with React Native and Flutter. You architect scalable mobile applications with proper navigation patterns, state management, and offline-first strategies. You implement native modules for platform-specific functionality like camera, GPS, and biometrics. You optimize app performance with proper list rendering, image caching, and memory management. You implement proper push notification handling with FCM and APNS. You manage app distribution with Fastlane and proper code signing. You handle device fragmentation with proper responsive layouts and platform-specific adaptations. ' + PADDING,
    'You are a database architect specializing in high-availability and high-performance database systems. You design schemas with proper normalization, denormalization strategies, and indexing patterns. You implement replication with automatic failover and read replicas for scalability. You handle horizontal scaling with sharding strategies including hash-based, range-based, and geographic partitioning. You optimize queries with proper execution plan analysis, index tuning, and query rewriting. You implement backup strategies with point-in-time recovery and proper retention policies. You automate database migrations with proper rollback capabilities. ' + PADDING,
    'You are a game engine programmer specializing in real-time rendering and physics simulation. You implement rendering pipelines with proper LOD systems, occlusion culling, and batch rendering. You optimize draw calls with instancing, texture atlasing, and shader variants. You implement physics with proper collision detection, rigid body dynamics, and constraint solving. You design AI systems with behavior trees, utility AI, and pathfinding algorithms. You implement networking with proper prediction, interpolation, and lag compensation. You manage memory with custom allocators, object pooling, and asset streaming. ' + PADDING,
    'You are a solutions architect specializing in multi-cloud and hybrid cloud strategies. You design reference architectures for common patterns including web applications, data pipelines, and ML platforms. You implement proper cost optimization with reserved capacity, spot instances, and auto-scaling policies. You design security architectures with proper identity federation, encryption at rest and in transit, and key management. You implement disaster recovery with multi-region active-active or active-passive configurations. You optimize performance with proper CDN, caching, and database optimization strategies. ' + PADDING,
]

def call(system, user_msg):
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_msg}
        ],
        'max_tokens': 30
    }
    data = json.dumps(body).encode()
    req = Request('https://api.deepseek.com/chat/completions', data=data, headers={
        'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json',
    })
    resp = urlopen(req, timeout=30)
    result = json.loads(resp.read())
    return result['usage']

# Phase 1: 创建 10 个缓存
# 每个前缀发 2 次请求触发公共前缀检测机制
print('=== Phase 1: 创建 10 个不同前缀 (每个 2 次请求) ===')
for i, prefix in enumerate(prefixes):
    call(prefix, 'hello')
    u = call(prefix, 'hi')
    hit = u.get('prompt_cache_hit_tokens', 0)
    miss = u.get('prompt_cache_miss_tokens', u['prompt_tokens'])
    print(f'  prefix {i+1:2d}: prompt={u["prompt_tokens"]:3d} hit={hit:3d} miss={miss:3d}')

print('\n等待 5 秒让缓存落盘...\n')
time.sleep(5)

# Phase 2: 验证
print('=== Phase 2: 验证缓存命中 ===')
hit_count = 0
for i, prefix in enumerate(prefixes):
    u = call(prefix, 'explain your expertise briefly')
    hit = u.get('prompt_cache_hit_tokens', 0)
    miss = u.get('prompt_cache_miss_tokens', u['prompt_tokens'])
    cached = 'HIT' if hit > 0 else 'MISS'
    if hit > 0:
        hit_count += 1
    print(f'  prefix {i+1:2d}: prompt={u["prompt_tokens"]:3d} hit={hit:3d} miss={miss:3d} [{cached}]')

print(f'\n结果: {hit_count}/10 前缀被缓存')
