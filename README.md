# ANP 本地小说创作与审核工作台

ANP 是一个本地优先的小说创作、审核与管理工具。它提供 ANP Local Studio 本地管理界面，支持单篇/批量生成、SQLite 队列、Dashboard 统计、AI 七维审核、最多 3 次自动重写、人工审核、日志查看、APScheduler 调度和 Windows 一键启动脚本。

> 当前范围：本地生成、本地审核、本地管理。项目只处理本地创作与审核，不处理第三方内容平台提交、平台登录或账号托管流程。

## 0. 一键启动本地软件界面

Windows 双击或在命令行运行：

```bat
start_anp.bat
```

PowerShell 也可以：

```powershell
.\start_anp.ps1
```

启动脚本会自动检查/创建 `.venv`、安装 `requirements.txt`、初始化 `data/`、`logs/` 和 SQLite 数据库，然后启动本地 Web 管理界面。

默认访问：

```text
http://127.0.0.1:8000
```

如果端口被占用：

```bat
set ANP_REVIEW_PORT=18000
start_anp.bat
```

ANP Local Studio 包含：总览 Dashboard、单篇/批量生成、审核队列、作品详情编辑、批准/拒绝/AI 审核、最近日志和配置说明。页面不会展示 API key、账号密码或其他敏感配置。

## 1. 本地开发环境

建议使用 Python 3.11+。

```bash
cd D:\Development_alma\anp
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

所有命令均可在无 DeepSeek key 的情况下通过 mock/dry-run 路径运行，方便本地验收。

## 2. 配置

默认读取 `config.yaml`，也可用 `ANP_CONFIG` 指向私有配置文件：

```bash
set ANP_CONFIG=D:\secure\anp-config.yaml
```

敏感字段只允许来自私有配置文件或环境变量，禁止写入源码：

```bash
set DEEPSEEK_API_KEY=your_deepseek_key
```

常用配置项：

```yaml
deepseek:
  api_key: ""          # env: DEEPSEEK_API_KEY
  mock: true           # 无 key 时自动 mock
runtime:
  dry_run: true
database:
  sqlite_path: "data/anp.sqlite3"
  backup_dir: "data/backups"
logging:
  file: "logs/anp.log"
```

可覆盖的环境变量包括：`ANP_SQLITE_PATH`、`ANP_DRY_RUN`、`ANP_MOCK_DEEPSEEK`、`ANP_AI_REVIEW_THRESHOLD`、`ANP_MAX_REWRITE_ATTEMPTS`、`ANP_MONTHLY_BUDGET_CNY`、`ANP_DAILY_TOKEN_LIMIT`。

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

### ANP Local Studio 管理界面

```bash
python -m review_queue.human_review
# 默认 http://127.0.0.1:8000
# 如端口占用：python -m review_queue.human_review --port 18000
```

管理界面包含总览 Dashboard、生成区、审核区、日志区和设置说明区。按钮有 loading 状态、重复点击保护、toast 反馈、错误展示和空状态提示。

### AI 审核 dry-run / mock

```bash
python -m cli.ai_review --limit 20
python -m cli.ai_review --limit 20 --threshold 85
```

AI 审核包含 7 个维度：`plot`、`character`、`pacing`、`language`、`originality`、`safety`、`platform_fit`。低于阈值会自动重写，最多 3 次；仍不通过则转为 `needs_human`。无 DeepSeek key 时自动使用 mock/dry-run，不阻塞验收。

## 5. 统一入口

`main.py` 当前支持初始化配置、初始化数据库和手动 SQLite 备份：

```bash
# 初始化配置、数据库，并提示本地 UI 入口
python main.py

# 手动备份 SQLite 后退出
python main.py --backup-now
```

> `--mode auto / semi-auto / once` 等旧调度模式已随 APScheduler 整体移除。
> 生成、AI 审核等操作可以通过本地 UI 的“立即执行一次”按钮，或直接使用 `cli/generate.py`、`cli/batch_generate.py`、`cli/ai_review.py` 等独立 CLI 触发。

SQLite 备份写入 `database.backup_dir`。

## 6. 本地验证

推荐在提交前执行：

```bash
pytest -q
python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py cli/*.py
```

生成与审核的 smoke test：

```bash
set ANP_SQLITE_PATH=data/local_verify.sqlite3 && python -m cli.batch_generate --count 3 --theme 本地验收 --word-count 600 --dry-run --print-ids
set ANP_SQLITE_PATH=data/local_verify.sqlite3 && python -m cli.ai_review --limit 5
```

## 7. 人工配置项与安全说明

必须人工准备或确认：

- DeepSeek API key（如需真实生成/审核）。
- 调度 cron、AI 阈值、成本上限。
- SQLite 数据库路径、备份目录、日志目录权限。

安全要求：

- 不提交 `.env`、真实账号密码、API key、本地数据库、日志或浏览器数据。
- 日志只记录动作、story_id、状态与错误，不记录密钥。
- 生产/私有配置应放在仓库外，或通过环境变量注入。
- 提交前建议运行 secret 扫描，确认没有敏感信息进入 Git 历史。

## 8. 常见问题

**没有 DeepSeek key 会失败吗？**  
不会。配置加载器会警告并启用 mock/dry-run，生成和 AI 审核仍可本地验证。

**8000 端口被占用？**  
使用 `python -m review_queue.human_review --port 18000` 或设置 `ANP_REVIEW_PORT=18000`。

**数据保存在哪里？**  
默认保存在 `data/anp.sqlite3`。`data/` 属于本地运行数据，不应提交到 GitHub。

**多平台和外部通知在哪里？**  
当前不在项目范围内。如后续需要，应作为独立模块设计，并单独做安全评审。

## 9. Git 检查

提交前建议确认工作区和最近提交：

```bash
git status --short
git log --oneline -1
```
