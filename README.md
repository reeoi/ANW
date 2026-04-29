# ANP 全自动小说创作与发布流水线

ANP 是本地运行的小说生成、审核、发布流水线。本仓库当前完成到 Sprint 6：可生成小说进入 SQLite 队列，通过本地 FastAPI 人工审核页面处理，运行 AI 七维评分/自动重写，通过 Playwright 发布基类与番茄小说适配器执行 dry-run 或安全暂停式发布，并可由 APScheduler 串联生成、AI 审核、随机延迟发布和 SQLite 备份。

## 本地安装

```bash
cd D:\Development_alma\anp
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

发布层已使用 Playwright。安装浏览器运行时：

```bash
python -m playwright install
# 如只安装 Chromium：python -m playwright install chromium
```

## 配置密钥

默认配置文件为 `config.yaml`。所有 API key、密码、平台账号和登录态路径只允许来自 `config.yaml` 或环境变量，不应写入源码。

推荐用环境变量覆盖敏感字段：

```bash
set DEEPSEEK_API_KEY=your_deepseek_key
set FANSQ_USERNAME=your_username
set FANSQ_PASSWORD=your_password
set FANSQ_LOGIN_STATE_PATH=data/browser/fansq_state.json
```

也可用 `ANP_CONFIG` 指向其他配置文件：

```bash
set ANP_CONFIG=D:\secure\anp-config.yaml
```

## Mock / dry-run

`config.yaml` 默认启用：

```yaml
runtime:
  dry_run: true
deepseek:
  mock: true
```

如果没有配置 `DEEPSEEK_API_KEY`，统一配置加载器会给出清晰 warning，并自动进入 mock/dry-run，不阻塞进程启动。

## 常用命令

```bash
# 加载配置、初始化 SQLite、显示运行模式
python main.py --mode semi-auto
python main.py --mode auto

# dry-run 生成示例文本
python -m cli.generate --theme 雨夜归人 --word-count 3000

# 对 pending 队列运行 AI 审核（七维评分、自动重写、转人工）
python -m cli.ai_review --limit 20

# dry-run 发布检查：读取一条 approved 作品，模拟成功；默认保留 approved 状态
python -m cli.publish --dry-run --dry-run-outcome success

# dry-run 模拟验证码/滑块/登录态缺失，保存截图并暂停；默认保留状态
python -m cli.publish --dry-run --dry-run-outcome paused

# 如需把 dry-run 结果写回 SQLite（例如 publish_paused）
python -m cli.publish --dry-run --dry-run-outcome paused --commit-dry-run

# 真实模式：需要提前准备 login_state_path；遇到登录/验证码/滑块/风控会暂停
python -m cli.publish --real

# 启动人工审核页面（默认 http://localhost:8000）
python -m queue.human_review

# 如果 8000 端口已被其他本地服务占用，可仅改绑定端口；默认仍是 8000
python -m queue.human_review --port 18000

# 语法检查
python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py publisher/*.py cli/*.py
```


## 统一启动（Sprint 6）



```bash

# 全自动模式：启动 APScheduler，按 config.yaml 串联生成、AI 审核、随机延迟发布、SQLite 备份

python main.py --mode auto



# 半自动模式：初始化本地环境，并给出人工审核服务启动方式

python main.py --mode semi-auto

python -m queue.human_review



# 无 DeepSeek key、无番茄登录态也可跑通的本地端到端模拟

python main.py --mode auto --dry-run



# 只跑一次本地流水线后退出，便于验收/CI

python main.py --mode auto --once



# 手动执行 SQLite 备份

python main.py --backup-now --mode semi-auto

```



关键日志写入 `config.yaml` 的 `logging.file`（默认 `logs/anp.log`）。人工审核首页底部会显示该日志文件位置与近期日志。



## 调度与风险控制配置



`config.yaml` 中与 Sprint 6 相关的配置项：



```yaml

scheduler:

  enabled: false

  timezone: "Asia/Shanghai"

  generate_cron: "0 9 * * *"

  review_cron: "30 9 * * *"

  publish_cron: "0 10 * * *"

  backup_cron: "0 3 * * *"



publisher:

  fansq:

    min_publish_interval_minutes: 5

    max_publish_interval_minutes: 15



audit:

  approval_threshold: 85

  max_rewrite_attempts: 3



database:

  backup_dir: "data/backups"

  daily_backup: true



cost_limits:

  monthly_budget_cny: 100

  daily_token_limit: 200000

```



发布任务不会在 `publish_cron` 到点立即提交，而是在 `min_publish_interval_minutes` 到 `max_publish_interval_minutes` 范围内随机延迟（默认 5-15 分钟）。AI 通过阈值可用 `audit.approval_threshold` 或 `ANP_AI_REVIEW_THRESHOLD` 调整；API 月限额配置可用 `cost_limits.monthly_budget_cny` 或 `ANP_MONTHLY_BUDGET_CNY` 覆盖。




`queue/ai_review.py` 会从 `config.yaml` 的 `audit` 与 `deepseek` 段读取阈值、最大重写次数和模型参数；也支持环境变量覆盖：

```bash
set ANP_AI_REVIEW_THRESHOLD=80
set ANP_MAX_REWRITE_ATTEMPTS=3
set ANP_AI_REVIEW_MODEL=deepseek-chat
set ANP_AI_REVIEW_TEMPERATURE=0.3
set ANP_AI_REVIEW_TIMEOUT_SECONDS=60
```

默认通过阈值为 80/100，默认最大重写次数为 3。审核 JSON 至少包含 `total_score`、`dimension_scores`、`issues`、`suggestions`、`decision`；7 个维度为 `plot`、`character`、`pacing`、`language`、`originality`、`safety`、`platform_fit`。低于阈值时系统会自动重写并增加 `retry_count`，通过后写入 `status='approved'`；达到最大重写次数仍失败时写入 `status='needs_human'`，并在 `review_notes` 保存问题列表。无 DeepSeek key 时会使用稳定 mock/dry-run 路径模拟评分、通过、重写和转人工。

## 人工审核页面

启动本地审核服务：

```bash
python -m queue.human_review
```

然后在浏览器访问 `http://localhost:8000`。`python -m queue.human_review` 默认绑定 `127.0.0.1:8000`；如本机 8000 已被其他服务占用，可使用 `python -m queue.human_review --port 18000` 或设置 `ANP_REVIEW_PORT=18000` 后访问对应端口。首页只列出 SQLite 中 `status='pending'` 或 `status='needs_human'` 的作品，并显示标题、内容、状态、分数、重试次数和审核备注。

可执行操作：

- **approve / 批准**：将作品状态更新为 `approved`，进入后续发布阶段。
- **reject / 拒绝**：将作品状态更新为 `rejected`，并写入人工拒绝备注。
- **保存编辑**：修改标题、内容和审核备注；标题和内容不能为空，页面输出会进行 HTML 转义。
- **运行 AI 审核批次**：调用 `queue.ai_review.run_review_batch` 的 dry-run/mock 批处理逻辑；没有 `pending` 数据时页面会显示“没有可审核数据”的友好提示。

页面不会展示 DeepSeek key、平台账号、密码或登录态路径。操作日志只记录动作类型和 story_id / 批次计数，不记录敏感配置值。

## 项目结构

```text
anp/
├── config.yaml
├── config_loader.py
├── generator/
│   ├── __init__.py
│   ├── api_client.py
│   └── prompt_builder.py
├── queue/
│   ├── __init__.py
│   ├── db.py
│   ├── models.py
│   ├── human_review.py
│   └── ai_review.py
├── publisher/
│   ├── __init__.py
│   ├── base_publisher.py
│   └── fansq.py
├── cli/
│   ├── __init__.py
│   ├── generate.py
│   ├── batch_generate.py
│   ├── ai_review.py
│   └── publish.py
├── docs/
│   └── fansq_workflow.md
├── scheduler.py
├── main.py
├── requirements.txt
└── anp制作清单.md
```

## 安全边界

- 源码不硬编码 API key、密码或平台账号。
- DeepSeek key 可由 `deepseek.api_key` 或 `DEEPSEEK_API_KEY` 提供。
- 番茄账号、密码、登录态路径可由 `config.yaml` 或环境变量提供。
- 发布自动化只做正常浏览器操作，遇到验证码、滑块、登录态缺失或风控页面必须暂停、截图到 `logs/screenshots`、记录日志到 `logs/anp.log`，不绕过、不破解、不调用打码。

## Sprint 4 验证记录

2026-04-29 本地验证通过：

```bash
pytest -q tests/test_sprint4.py
# 5 passed
python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py publisher/*.py cli/*.py
# exit code 0
```

覆盖路径：mock 七维 JSON 评分、低分自动重写后通过、达到重写上限转人工、环境变量覆盖阈值/模型参数、CLI 批处理输出 reviewed/approved/needs_human/failed/failure_reasons。


## Sprint 5 验证记录

2026-04-29 本地验证通过：

```bash
pytest -q tests/test_sprint5.py
# 5 passed
python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py publisher/*.py cli/*.py
# exit code 0
```

覆盖路径：Playwright 发布基类日志/截图/暂停结果封装、番茄 dry-run 成功与暂停、真实模式登录态缺失安全暂停、CLI 读取一条 approved 记录、dry-run 默认保留状态与 `--commit-dry-run` 写入 `publish_paused`。

## Sprint 6 验证记录

2026-04-29 本地验证通过：

```bash
pytest -q tests/test_sprint6.py
# 6 passed
pytest -q
# 23 passed
python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py publisher/*.py cli/*.py
# exit code 0
python main.py --mode auto --dry-run
# 可在无 DeepSeek key、无番茄登录态下完成本地生成 -> AI 审核 -> dry-run 发布模拟
```

覆盖路径：APScheduler 生成/AI 审核/发布窗口/SQLite 备份任务注册，5-15 分钟随机发布延迟，API 月限额读取，SQLite 备份，`main.py --mode auto --dry-run` 本地端到端模拟，`main.py --mode semi-auto` 明确给出人工审核启动方式，关键阶段日志写入配置文件。

## Git 状态

Sprint 6 已完成阶段性提交；若 evaluator 环境看不到提交，请运行 `git log --oneline -1` 确认。
