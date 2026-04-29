# ANP 全自动小说创作与发布流水线

ANP 是一个本地优先的小说创作、审核与发布 MVP。当前交付到 Sprint 7：支持单篇/批量生成写入 SQLite 队列、FastAPI 人工审核、AI 七维审核与最多 3 次重写、番茄小说 Playwright 发布适配器的 dry-run/安全暂停、APScheduler 调度、验证记录与交接清单。

> 范围声明：本轮只完成本地 MVP 与番茄小说单平台适配。Telegram/飞书通知、起点/公众号等多平台发布、数据统计面板列为后续范围。

## 1. 安装

```bash
cd D:\Development_alma\anp
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install
# 如只需要 Chromium：python -m playwright install chromium
```

建议使用 Python 3.11+。所有命令均可在无 DeepSeek key、无番茄登录态时通过 mock/dry-run 路径运行。

## 2. 配置

默认读取 `config.yaml`，也可用 `ANP_CONFIG` 指向私有配置文件：

```bash
set ANP_CONFIG=D:\secure\anp-config.yaml
```

敏感字段只允许来自 `config.yaml` 或环境变量，禁止写入源码：

```bash
set DEEPSEEK_API_KEY=your_deepseek_key
set FANSQ_USERNAME=your_username
set FANSQ_PASSWORD=your_password
set FANSQ_LOGIN_STATE_PATH=data/browser/fansq_state.json
```

常用配置项：

```yaml
deepseek:
  api_key: ""          # env: DEEPSEEK_API_KEY
  mock: true           # 无 key 时自动 mock
runtime:
  mode: "semi-auto"    # auto | semi-auto
  dry_run: true
publisher:
  fansq:
    login_state_path: "data/browser/fansq_state.json"
    draft_url: "https://fanqienovel.com/"
    min_publish_interval_minutes: 5
    max_publish_interval_minutes: 15
    pause_on_risk_control: true
scheduler:
  generate_cron: "0 9 * * *"
  review_cron: "30 9 * * *"
  publish_cron: "0 10 * * *"
  backup_cron: "0 3 * * *"
database:
  sqlite_path: "data/anp.sqlite3"
  backup_dir: "data/backups"
logging:
  file: "logs/anp.log"
  screenshot_dir: "logs/screenshots"
```

可覆盖的环境变量包括：`ANP_SQLITE_PATH`、`ANP_DRY_RUN`、`ANP_MOCK_DEEPSEEK`、`ANP_AI_REVIEW_THRESHOLD`、`ANP_MAX_REWRITE_ATTEMPTS`、`ANP_MONTHLY_BUDGET_CNY`、`ANP_DAILY_TOKEN_LIMIT`、`ANP_MIN_PUBLISH_INTERVAL_MINUTES`、`ANP_MAX_PUBLISH_INTERVAL_MINUTES`。

## 3. 生成

单篇生成并写入 `status='pending'` 队列：

```bash
python -m cli.generate --theme 雨夜归人 --word-count 3000 --style 现实温情
```

批量生成 N 篇并写入队列：

```bash
python -m cli.batch_generate --count 5 --theme 海边旧书店 --word-count 1200 --style 悬疑温情 --dry-run --print-ids
```

批量命令会输出 `requested/success/failed/dry_run/database/failure_reasons`。`--dry-run` 会强制使用本地 mock 生成，即使机器上配置了真实 DeepSeek key。`--count` 和 `--word-count` 必须为正数。

## 4. 审核

### 人工审核

```bash
python -m queue.human_review
# 默认 http://127.0.0.1:8000
# 如端口占用：python -m queue.human_review --port 18000
```

页面只展示 `pending` 和 `needs_human` 作品，支持批准、拒绝、编辑、运行 AI 审核批次。页面不展示 API key、平台账号、密码或登录态路径。

### AI 审核 dry-run / mock

```bash
python -m cli.ai_review --limit 20
python -m cli.ai_review --limit 20 --threshold 85
```

AI 审核包含 7 个维度：`plot`、`character`、`pacing`、`language`、`originality`、`safety`、`platform_fit`。低于阈值会自动重写，最多 3 次；仍不通过则转为 `needs_human`。无 DeepSeek key 时自动使用 mock/dry-run，不阻塞验收。

## 5. 发布

发布 CLI 只读取一条 `status='approved'` 的作品：

```bash
# dry-run 成功，默认保留 approved 状态，便于重复测试
python -m cli.publish --dry-run --dry-run-outcome success

# dry-run 模拟验证码/滑块/登录态缺失导致暂停，默认保留状态
python -m cli.publish --dry-run --dry-run-outcome paused

# 如需把 dry-run 暂停结果写回 SQLite
python -m cli.publish --dry-run --dry-run-outcome paused --commit-dry-run

# 真实 Playwright 模式，需先准备 login_state_path
python -m cli.publish --real
```

真实模式下 Sprint 7 MVP 只做正常浏览器自动化和草稿填充/安全暂停，不做风控绕过。最终提交、分类、章节规则建议人工复核。

## 6. 调度与统一入口

```bash
# 半自动：初始化配置、数据库，并提示人工审核入口
python main.py --mode semi-auto

# 全自动：启动 APScheduler，按 cron 串联生成、AI 审核、随机延迟发布、备份
python main.py --mode auto

# 本地端到端模拟，无 key/登录态也可运行
python main.py --mode auto --dry-run

# 只跑一次流水线后退出，便于验收/CI
python main.py --mode auto --once

# 手动备份 SQLite
python main.py --backup-now --mode semi-auto
```

发布任务会在配置的 5-15 分钟窗口内随机延迟，降低集中触发风险。SQLite 备份写入 `database.backup_dir`。

## 7. dry-run 验证与本次验收记录

2026-04-29 Sprint 7 本地验收执行结果：

```bash
pytest -q tests/test_sprint7.py
# 2 passed

pytest -q
# 25 passed

python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py publisher/*.py cli/*.py
# exit code 0

set ANP_SQLITE_PATH=data/sprint7_batch_dryrun.sqlite3 && python -m cli.batch_generate --count 3 --theme Sprint7批量 --word-count 600 --dry-run --print-ids
# Batch generation completed: requested=3 success=3 failed=0 dry_run=True database=data\sprint7_batch_dryrun.sqlite3
# failure_reasons=none

set ANP_SQLITE_PATH=data/sprint7_verify.sqlite3 && python -m cli.generate --theme Sprint7单篇 --word-count 600 --style 验收 --print-content
# Generated story ID: 1; Status: pending; exit code 0

set ANP_SQLITE_PATH=data/sprint7_verify.sqlite3 && python -m cli.ai_review --limit 5
# reviewed=1; approved=1; needs_human=0; failed=0; failure_reasons=none; exit code 0

set ANP_SQLITE_PATH=data/sprint7_verify.sqlite3 && python -m cli.publish --dry-run --dry-run-outcome success
# story_id=1 platform=fansq status=published status_preserved ...; exit code 0
```

说明：`cli.publish --dry-run` 默认不改写队列状态，输出 `status_preserved`，用于重复验收；需要落库时使用 `--commit-dry-run`。

## 8. 番茄验证码/滑块/登录态缺失策略

ANP 明确禁止绕过平台风控。遇到以下情况必须暂停：验证码、图形验证码、滑块、扫码/短信验证、登录态缺失或过期、安全验证、异常访问、风控页、异常跳转、未识别编辑器、Playwright 超时。

暂停策略与 `docs/fansq_workflow.md` 保持一致：

1. 立即停止后续自动点击/提交。
2. 保存截图或占位证据到 `logs/screenshots` 或 `logging.screenshot_dir`。
3. 写日志到 `logs/anp.log` 或 `logging.file`，包含 platform、story_id、原因、截图路径。
4. CLI 输出需要人工处理的通知；当前 MVP 未接入外部通知，Telegram/飞书为后续范围。
5. 等待人工完成登录/验证/页面复核后再重试。
6. 不破解、不调用打码平台、不模拟拖动滑块、不绕过登录或风控。

## 9. 人工配置项与安全说明

必须人工准备或确认：

- DeepSeek API key（如需真实生成/审核）。
- 番茄小说账号权限与登录态 `data/browser/fansq_state.json`。
- 番茄作品分类、章节设置、敏感词与最终提交按钮。
- 调度 cron、发布间隔、AI 阈值、成本上限。
- SQLite 数据库路径、备份目录、日志目录权限。

安全要求：

- 不提交 `.env`、登录态、真实账号密码、API key、浏览器 storage state。
- `data/browser/` 建议作为本机私有目录。
- 日志只记录动作、story_id、状态与错误，不记录密钥。
- 发布自动化只做合规浏览器操作；任何风控信号都转人工。

## 10. 常见问题

**没有 DeepSeek key 会失败吗？**  
不会。配置加载器会警告并启用 mock/dry-run，生成和 AI 审核仍可本地验证。

**发布命令提示没有 approved 作品怎么办？**  
先运行生成和 AI 审核，或在人工审核页面批准一篇作品。

**dry-run 发布为什么没有把状态改成 published？**  
默认保留 `approved`，便于重复测试。需要写回请加 `--commit-dry-run`。

**遇到验证码/滑块能否自动处理？**  
不能。系统会暂停、截图、写日志并要求人工处理，明确禁止绕过。

**8000 端口被占用？**  
使用 `python -m queue.human_review --port 18000` 或设置 `ANP_REVIEW_PORT=18000`。

**多平台和外部通知在哪里？**  
未在 Sprint 7 实现，列为后续扩展范围。

## 11. Git 状态

Sprint 7 已完成最终阶段提交：`git commit -m "Complete sprint 7 delivery package"`。如验收环境看不到提交，请运行：

```bash
git log --oneline -1
git status --short
```
