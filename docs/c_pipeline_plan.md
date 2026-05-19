# ANP C-Pipeline 重构方案

> 由 grill-me 拷问产出的实施计划。前置决策见本文件「锁定决策」节,任何修改前请重新拷问。
> 创建日期: 2026-05-06
> 状态: 待编码

---

## 1. 背景与目标

### 1.1 现状

ANP 当前是「单步 prompt 调 DeepSeek 生成 3000 字短篇」的简单 pipeline:

```
cli/generate --theme X --style Y → DeepSeek 1 次调用 → SQLite content blob → 7 维 AI 审核 → 番茄发布
```

问题:
- 字数 3000 不符合番茄短篇主流字段(8000-15000)
- 单次 prompt 出全篇,情绪曲线/反转设计/伏笔回收没有结构保障
- AI 腔重(DeepSeek 自身倾向),无去味流程
- 题材/风格 CLI 硬传,无自动选题
- 发布时间过于规律(每天 10:00),反风控弱

### 1.2 目标

整合 oh-story-claudecode 的短篇写作方法论,把 ANP 升级为 6-phase 多阶段 pipeline,产出 8000-12000 字符合番茄短篇标准的稿件,并实现:
- 题材/风格 LLM 自动生成(每周扫榜+演化题材池)
- 发布时间真正随机(每天 0-5 篇,9-22 点随机时刻)
- 多 phase 中间产物可审计、可续跑、可观测

---

## 2. 锁定决策(25 项)

| # | 决策项 | 锁定值 | 备注 |
|---|---|---|---|
| 1 | 写作复杂度 | C 全流程重构 | 8000-15000 字范围 |
| 2 | LLM 引擎 | D1 纯 DeepSeek | 1M 上下文 + 缓存价友好 |
| 3 | 启用 skill | story-short-write + story-deslop 必上 | 核心 |
| 4 | 启用 skill | story-short-scan 做后台任务 | F1 路线 |
| 5 | 不启用 skill | story-short-analyze + story-cover | MVP 不上 |
| 6 | scan 数据来源 | F1 静态种子库 + LLM 演化 | 不抓站 |
| 7 | scan 频次 | 每周一 03:00,产 100 题材 | |
| 8 | scan 失败处理 | a+c: 回退上周 pool,无 pool 则阻塞 | 不回退静态种子 |
| 9 | scan 种子库 | 由 Claude 从 oh-story `real-market-data.md` 蒸馏成 `data/scan_seeds.yaml` | |
| 10 | 状态存储 | E3 混合: SQLite 状态机 + 文件系统大产物 | |
| 11 | 失败恢复 | Phase 0-2 直 fail / Phase 3-5 节级自动重试 ≤3 次 | |
| 12 | work_dir 留存 | 永久保留,不归档不清理 | |
| 13 | 单篇字数 | W3 用 theme_pool item 自带 target_length(默认 [8000,12000]) | |
| 14 | 分节策略 | N2 LLM 自定每节字数 + 代码硬校验 | 节数 8-15、每节 800-1500、总字数 ±10% |
| 15 | Phase 3 上下文 | C3 全前文喂(设定 + 大纲 + 全部已写节) | 靠 prompt cache 摊薄 |
| 16 | 节级硬校验 | 启用三项: 字数 ≥800 / AI 腔黑名单 0 命中 / 段长 ≤60 字 | 不达标重试 ≤2 再 needs_human |
| 17 | 发布随机 | R3-限时: 每天 random_int[0,5] 篇,9:00-22:00 随机时刻 | gap ≥30 min,塞不下降 n |
| 18 | 队列空 slot | 跳过,不积压 | |
| 19 | 队列爆量 | 严格按计划只发 N,多余留次日 | |
| 20 | 多篇优先级 | FIFO + 跨日情绪平衡 | |
| 21 | 篇数分布 | 均匀 uniform_int[0,5] | |
| 22 | 模型路由 | M2 全程 v4-pro(含思考模式) | 触顶降级到 flash |
| 23 | 月预算 | B2 = 100 CNY/月 | 触顶切高流量 phase 到 flash |
| 24 | daily_token_limit | 800000 | |
| 25 | 老代码 | L1 全删 + L2 不留 legacy 路径 | |
| 26 | CLI 参数 | P2 可选 override(`--theme/--style/--word-count` 不传则自动) | |
| 27 | Web UI | U2 加 phase 进度条 + work_dir 文件浏览 + 续跑按钮 | |
| 28 | AI 审核读哪 | A2 改读 `final_content_path` 文件,content 列废弃 | |
| 29 | 审核阈值 | T2 由 85 上调到 90 | |
| 30 | 老数据 | D2 全删,迁移前备份到 `data/legacy_backup_YYYYMMDD.sqlite3` | |
| 31 | 重写策略 | R2 仅重跑 Phase 4-5,最多 3 次 | |
| 32 | 并发策略 | K2 同时最多 2 篇 pipeline 并行,phase 内串行 | |

### 2.1 隐形决策(默认采纳)

- **theme_pool 冷启动**: 第一次 `cli/generate` 检测 pool 不存在 → 自动同步触发一次 scan 阻塞等待
- **Phase 1 标题**: theme_pool 的 `hint_title` 仅作启发,Phase 1 输出 `final_title` 覆写
- **失败 phase 续跑**: Web UI 加「续跑 from phase X」按钮,需要时人工修 `2_小节大纲.md` 后续跑
- **Phase 1 同步出简介**: Phase 1 同时产出 `summary`(150-300 字,按 real-market-data.md 简介公式),写入 `1_设定.md` 头部供番茄发布表单使用
- **延后处理项**(用户暂不提供更多建议): 通知机制、cost 实时监控、多账号番茄分发、跨平台扩展、备份策略

---

## 3. 架构与数据流

### 3.1 Pipeline 总览

```
┌─────────────────────────────────────────────────────────────┐
│ [每周一 03:00] weekly_scan.py                                │
│   读 scan_seeds.yaml → DeepSeek-V4-Pro 演化 → 100 题材       │
│   写 data/theme_pool.json                                    │
│   失败时: 回退上周 pool / 无 pool 阻塞                       │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ [每天 03:00] plan_today_publishes.py                         │
│   抽 n=uniform[0,5],在 9-22 点抽 n 个不重叠时刻             │
│   gap≥30min 不够则降 n,写 daily_publish_plan 表             │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ [由调度器/CLI 触发] c_pipeline.orchestrator.run_pipeline()   │
│   Phase 0 选题 (pro)         → 0_选题.json                   │
│   Phase 1 框架+反转 (pro+thinking) → 1_设定.md               │
│   Phase 2 大纲 (pro+thinking) → 2_小节大纲.md (校验,失败重试2)│
│   Phase 3 逐节 (pro,C3 全前文) → 3_正文_第 N 节.md ×N         │
│             (每节校验,失败重试2,K2 篇间并发 ≤2)              │
│   Phase 4 精修 (pro+thinking) → 4_精修稿.md                  │
│   Phase 5 去 AI 味 (pro)     → 5_最终稿.md                   │
│   写回 stories.final_content_path,status='pending'           │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ [现有] AI 审核 (cli/ai_review,读 final_content_path)         │
│   阈值 90,失败仅重跑 Phase 4-5 ≤3 次                         │
│   依然失败 → needs_human                                     │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ [现有] 人工审核 Web UI(改造:phase 进度 + work_dir 浏览)      │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ [发布触发] 每个 publish slot + 5-15 分钟随机延迟             │
│   FIFO 取 approved + 跨日情绪平衡                            │
│   Playwright 番茄发布(现有逻辑不动)                          │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 SQLite Schema(D2 全删后重建)

```sql
-- stories 表(新建)
CREATE TABLE stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/needs_human/approved/published/failed
    pipeline_version TEXT NOT NULL DEFAULT 'c1',
    work_dir TEXT NOT NULL,                  -- data/works/{id}/
    current_phase TEXT NOT NULL,             -- phase_0 / phase_3_section_05 / phase_5_done / failed_at_phase_2
    final_content_path TEXT,                 -- work_dir/5_最终稿.md
    pipeline_cost_cny REAL DEFAULT 0,
    target_length INTEGER,
    emotion TEXT,
    genre TEXT,
    hint_title TEXT,                         -- 来自 theme_pool
    summary TEXT,                            -- Phase 1 输出,150-300 字
    ai_review_score REAL,
    ai_review_attempts INTEGER DEFAULT 0,    -- 计 R2 重跑次数
    content TEXT,                            -- 兼容字段,新行恒 NULL
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_stories_status ON stories(status);
CREATE INDEX idx_stories_current_phase ON stories(current_phase);

-- daily_publish_plan 表(新建)
CREATE TABLE daily_publish_plan (
    date DATE PRIMARY KEY,
    planned_count INTEGER NOT NULL,
    slots_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- slots_json schema:
-- [{"slot_time":"2026-05-06T14:23:00","story_id":123,"published_at":"...","skipped_reason":null}]

-- pipeline_cost_log 表(新建,B2 触顶降级判断依据)
CREATE TABLE pipeline_cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER,
    phase TEXT,
    model TEXT,
    input_tokens INTEGER,
    cached_tokens INTEGER,
    output_tokens INTEGER,
    cost_cny REAL,
    occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(story_id) REFERENCES stories(id)
);
CREATE INDEX idx_cost_log_occurred_at ON pipeline_cost_log(occurred_at);
```

### 3.3 文件系统布局

```
D:\Development_alma\anp\
├── data/
│   ├── anp.sqlite3
│   ├── legacy_backup_20260506.sqlite3       (D2 删表前备份)
│   ├── theme_pool.json
│   ├── scan_seeds.yaml                      (从 real-market-data.md 蒸馏)
│   ├── browser/fansq_state.json             (现状)
│   ├── backups/                             (现状,SQLite 每日备份)
│   └── works/{story_id}/
│       ├── 0_选题.json                      (theme/emotion/style/refs_from_pool)
│       ├── 1_设定.md                        (核心框架+反转设计+人设+物件三现+final_title+summary)
│       ├── 2_小节大纲.md                    (N 行,每行: 主事件|子事件×3-5|情绪|读者新获知|钩子|伏笔/物件|动静|对话密度|target_words)
│       ├── 3_正文_第 01 节.md
│       ├── 3_正文_第 02 节.md
│       ├── ...
│       ├── 3_正文_合稿.md                   (Phase 3 收尾,合并所有节)
│       ├── 4_精修稿.md
│       ├── 5_最终稿.md                      (= final_content_path)
│       └── meta.json                        (每 phase 耗时/调用次数/token/cost)
├── logs/
│   ├── anp.log
│   └── screenshots/                         (现状)
└── (代码结构见第 4 节)
```

---

## 4. 代码结构

```
generator/
├── api_client.py                            (改造: v4-pro 支持 + 缓存命中统计 + 思考模式开关)
└── c_pipeline/                              (新增子包)
    ├── __init__.py
    ├── orchestrator.py                      (run_pipeline(story_id) 主入口,phase 状态机推进)
    ├── phase0_select.py                     (从 theme_pool 抽题 + LLM 微调)
    ├── phase1_framework.py                  (框架+反转设计+final_title+summary)
    ├── phase2_outline.py                    (小节大纲,含代码校验+重试)
    ├── phase3_sections.py                   (逐节生成,C3 全前文,节级校验+重试)
    ├── phase4_polish.py                     (整篇精修)
    ├── phase5_deslop.py                     (去 AI 味)
    ├── validators.py                        (字数/AI 腔/段长/节数守恒/总字数 ±10%)
    ├── cost_tracker.py                      (pipeline_cost_log 写入 + B2 触顶降级判断)
    ├── concurrency.py                       (K2 信号量,同时 ≤2 篇并行)
    └── prompts/                             (各 phase 模板,内嵌从 oh-story 蒸馏的子集)
        ├── phase0_select.txt
        ├── phase1_framework.txt
        ├── phase2_outline.txt
        ├── phase3_section.txt
        ├── phase4_polish.txt
        ├── phase5_deslop.txt
        ├── ai_slop_blacklist.json           (从 anti-ai-writing.md 提取的禁用词/比喻表)
        └── genre_formulas.json              (从 real-market-data.md 提取的 10 题材爆款公式)

scan/                                        (新增)
├── __init__.py
├── seed_evolver.py                          (weekly scan 主逻辑)
└── seeds.yaml -> ../data/scan_seeds.yaml    (符号链接,种子文件本身在 data/)

scheduler.py                                 (改造)
  ├── 删除 generate_cron 旧固定生成调度
  ├── 新增 plan_today_publishes 任务(每天 03:00)
  ├── 新增 weekly_scan 任务(每周一 03:00)
  └── publish 触发: 按 daily_publish_plan 的 slots 注册 DateTrigger

cli/
├── generate.py                              (改造: 走 c_pipeline.orchestrator,P2 可选 --theme/--style/--word-count)
├── batch_generate.py                        (改造: 同上,默认全自动)
├── publish.py                               (基本不动,只接 final_content_path)
├── ai_review.py                             (改造: 读 final_content_path,T2 阈值 90,R2 重跑 Phase 4-5)
├── scan_now.py                              (新增: 手动触发 weekly scan)
├── plan_today.py                            (新增: 手动触发 plan_today_publishes)
└── continue_pipeline.py                     (新增: 续跑 from phase X,UI 按钮调用)

review_queue/
├── human_review.py                          (改造: 加 U2 进度条 + work_dir 文件浏览 + 续跑按钮)
├── ai_review.py                             (改造: 阈值 90,R2 重跑)
├── db.py                                    (改造: schema 新字段)
├── models.py                                (改造: Story 数据类新字段)
└── notification_bus.py                      (现状)

publisher/
├── base_publisher.py                        (现状)
└── fansq.py                                 (改造: title/summary 从 work_dir/1_设定.md 读取或 stories 表读)

config.yaml                                  (改造,新增字段见 5.1)

tests/
├── test_c_pipeline_phase0.py                (新增)
├── test_c_pipeline_phase1.py                (新增,含 mock LLM 验证 final_title/summary 输出)
├── test_c_pipeline_phase2_validation.py     (新增,验证节数/字数硬校验)
├── test_c_pipeline_phase3_context.py        (新增,验证 C3 全前文上下文构造)
├── test_c_pipeline_validators.py            (新增,纯函数测试)
├── test_scan_evolver.py                     (新增)
├── test_plan_today_publishes.py             (新增,验证 0-5 分布 + gap)
├── test_concurrency.py                      (新增,验证 K2 信号量)
├── test_cost_tracker.py                     (新增,B2 触顶降级)
└── (现有 test_sprint*.py 全部移除或重写)
```

---

## 5. 配置变更

### 5.1 `config.yaml` 新增/修改字段

```yaml
deepseek:
  api_key: ""
  base_url: "https://api.deepseek.com"
  model: "deepseek-v4-pro"                   # 改: M2 全程 pro
  flash_model: "deepseek-v4-flash"            # 新: B2 降级目标
  thinking_mode: true                         # 新: pro 思考模式
  prompt_cache_enabled: true                  # 新: 1M 上下文缓存
  timeout_seconds: 120                        # 改: phase 4 大调用要更长
  max_retries: 3
  mock: true

audit:
  approval_threshold: 90                      # 改: T2 上调
  rewrite_strategy: "phase_4_5_only"          # 新: R2
  max_rewrite_attempts: 3                     # 改: R2 上限
  dimensions: { ... 现状 ... }

publisher:
  default_platform: "fansq"
  daily_count_distribution: "uniform"          # 新: R3 均匀分布
  daily_count_min: 0
  daily_count_max: 5
  operating_hours: ["09:00", "22:00"]
  slot_min_gap_minutes: 30
  fansq:
    enabled: true
    min_publish_interval_minutes: 5            # 现状,触发后单点抖动保留
    max_publish_interval_minutes: 15
    pause_on_risk_control: true

scheduler:
  enabled: true
  timezone: "Asia/Shanghai"
  weekly_scan_cron: "0 3 * * 1"               # 新: 每周一 03:00
  plan_today_cron: "0 3 * * *"                # 新: 每天 03:00
  generate_cron: ""                            # 删: 旧固定生成调度
  review_cron: "30 9 * * *"                   # 现状
  publish_cron: ""                             # 删: 改用 daily_publish_plan slots
  backup_cron: "0 4 * * *"

scan:
  pool_size: 100                              # 新
  on_failure: "fallback_or_block"             # 新: a+c
  seed_file: "data/scan_seeds.yaml"

c_pipeline:
  max_concurrent_pipelines: 2                 # 新: K2
  phase_2_max_retries: 2
  phase_3_section_max_retries: 2
  ai_slop_blacklist_path: "generator/c_pipeline/prompts/ai_slop_blacklist.json"
  genre_formulas_path: "generator/c_pipeline/prompts/genre_formulas.json"

cost_limits:
  monthly_budget_cny: 100.0                   # 改: B2
  daily_token_limit: 800000                   # 改
  on_budget_exceeded: "degrade"               # 改: 替代 stop_when_exceeded
  degrade_phases: ["phase_3", "phase_5", "ai_review", "weekly_scan"]  # 触顶时这些 phase 切 flash

logging:
  level: "INFO"
  file: "logs/anp.log"
  json: false
  screenshot_dir: "logs/screenshots"

database:
  sqlite_path: "data/anp.sqlite3"
  backup_dir: "data/backups"
  daily_backup: true
```

### 5.2 .env 不变

环境变量覆盖优先级保持现状。

---

## 6. 月成本估算(M2 + R3 均匀 [0,5] + R2 重跑 ≤3)

```
基础生成(假设 75 篇/月):
  Weekly scan:           0.5 CNY
  Phase 0 选题:          0.4 CNY
  Phase 1 框架+反转:     3.4 CNY  (pro,thinking)
  Phase 2 大纲:          3.2 CNY  (pro,thinking)
  Phase 3 逐节:         15.0 CNY  (pro,C3 全前文,缓存命中后)
  Phase 4 精修:         30.0 CNY  (pro,thinking,大调用)
  Phase 5 去 AI 味:     10.5 CNY  (pro)
  AI 审核:               2.0 CNY
小计:                   ~65 CNY

R2 重跑代价(假设 20% 篇触发,平均 1.5 次重跑):
  15 篇 × 1.5 × (Phase 4 + Phase 5)
  = 15 × 1.5 × (0.27 + 0.09 调到 pro 价)
  ≈ 12 CNY

合计:                  ~77 CNY/月  (B2 100 CNY 预算下 23% 余量)

极端高产月(150 篇,触顶降级):
  Phase 3/5/AI 审核切 flash → 节省约 35%
  合计:                ~100 CNY/月  (恰好触顶,degrade 生效)
```

---

## 7. 实施顺序(建议)

| 阶段 | 内容 | 验收点 |
|---|---|---|
| **A. 基础设施** | api_client.py 升级支持 v4-pro/缓存/思考模式;DB schema 迁移(D2 删表+建新表+备份);config.yaml 字段 | api_client 单测通过,新 schema 可读写,旧 sqlite 已备份 |
| **B. Scan 模块** | scan_seeds.yaml 蒸馏(Claude 一次性产出);seed_evolver.py;theme_pool.json schema | 跑一次 scan_now,产 100 条 theme_pool,字段齐全 |
| **C. Pipeline 核心** | orchestrator + 6 phase 模块 + validators + prompts/ + cost_tracker | dry-run 一篇全跑通,work_dir 各产物文件齐全 |
| **D. 调度改造** | plan_today_publishes 任务;daily_publish_plan 表;publish slot 注册逻辑 | 模拟 7 天调度,publish 时刻分布符合 R3 |
| **E. 集成接线** | cli/generate 改走 c_pipeline;ai_review 读文件 + R2 重跑;publisher 读 final_title/summary | 端到端 dry-run 通过 |
| **F. UI 升级** | review_queue/human_review 加进度条 + 文件浏览 + 续跑按钮 | UI 可视化测试通过 |
| **G. 监控与告警** | cost_tracker 触顶降级;K2 并发信号量;失败 work_dir 留存 | 模拟超预算场景,degrade 生效 |
| **H. 测试与验收** | 全套单测 + 端到端 dry-run + 真实 1 篇生成验证 | 测试覆盖 ≥80%,样稿人工 review 合格 |

---

## 8. 不在本次范围内(明确延后)

用户在拷问最后一轮表态:「还没有正式使用过,无法给出更多有效建议,先这样吧」,以下项明确**不**在本次实施:

- Telegram/飞书通知集成
- cost 实时监控仪表盘
- 多账号番茄分发(目前单账号)
- 跨平台扩展(知乎盐言/七猫/点众发布器)
- backup 保留策略(继续用现有每日备份)
- 番茄字体反爬(F1 路线下不抓站,无需破解)
- story-cover 封面生成
- story-short-analyze 拆文(不进自动 pipeline)

这些列入后续 Sprint 的候选,本次 spec 不预留 hook,实施时如需扩展再单独拷问。

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| DeepSeek v4-pro 思考模式输出不稳定(JSON 截断/格式漂移) | 中 | Phase 1/2 校验失败率高 | 校验失败时 prompt 补救 + 重试,3 次仍失败 needs_human |
| 缓存命中率低于预期(prefix 不匹配) | 中 | Phase 3 实际成本 2-5x 估算 | cost_tracker 监控命中率,B2 触顶降级 |
| Phase 4 精修过度改写(背离原意) | 中 | 稿件质量主观下降 | prompt 中明确「保留架构不变,只改语言」 + AI 审核 7 维守门 |
| theme_pool 题材同质化(LLM 演化收敛) | 中 | 长期生成风格雷同 | 演化 prompt 加反向多样性约束(emotion 分布/genre 分布)+ 观察 1 月后人工 review seeds.yaml |
| K2 并发下 SQLite 锁竞争 | 低 | 罕见死锁/重复入队 | 写操作走单线程 worker queue 而非多线程直写 |
| 番茄反风控对 R3 模式仍敏感 | 中 | 账号被限/封 | publish 内置 5-15 min 二次抖动保留;遇风控立即暂停;R3 上限 5/天可降配 |

---

## 10. 引用与基础

- oh-story-claudecode: `D:\BaiduNetdiskDownload\oh-story-claudecode-main\`
  - 蒸馏来源: `skills/story-short-write/SKILL.md`
  - 蒸馏来源: `skills/story-short-write/references/anti-ai-writing.md`(AI 腔黑名单)
  - 蒸馏来源: `skills/story-short-write/references/format-and-structure.md`(段落规则/对话规则)
  - 蒸馏来源: `skills/story-short-write/references/genre-writing-formulas.md`(题材公式)
  - 蒸馏来源: `skills/story-short-write/references/hook-techniques.md`(钩子)
  - 蒸馏来源: `skills/story-short-write/references/reversal-toolkit.md`(反转)
  - 蒸馏来源: `skills/story-short-write/references/villain-and-reveal.md`(反派揭露)
  - 蒸馏来源: `skills/story-short-write/references/writing-craft.md`(三层展开/结构物件)
  - 蒸馏来源: `skills/story-short-scan/references/real-market-data.md`(seeds.yaml 主源)
  - 蒸馏来源: `skills/story-deslop/`(去 AI 味方法)

- ANP 现状参考: `D:\Development_alma\anp\README.md` 与 `anp制作清单.md`

- DeepSeek 模型: v4-pro 1M 上下文,缓存命中 0.025 元/M(输入),输出 6 元/M;v4-flash 缓存命中 0.02 元/M,输出 2 元/M

---

> 本文件为拷问产出的最终决策。开始编码前如需修改任何锁定项,请重新拷问对应分支。
