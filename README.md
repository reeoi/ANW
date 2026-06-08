# ANP 本地小说创作与审核工作台

ANP 是一个本地优先的小说创作、审核和管理工具，面向短篇小说与长篇小说的持续生产流程。项目提供 ANP Local Studio 本地 Web 界面，支持题材管理、多阶段生成、AI 审查、人工复核、日志查看、预算控制、SQLite 队列和 Windows 一键启动。

> 当前范围：本地生成、本地审核、本地管理。本项目不托管平台账号，不处理第三方网页登录态，也不内置外部检测服务。

## 1. 快速启动

Windows 双击或在命令行运行：

```bat
start_anp.bat
```

PowerShell 也可以运行：

```powershell
.\start_anp.ps1
```

启动脚本会检查并创建 `.venv`，安装 `requirements.txt`，初始化 `data/`、`logs/` 和 SQLite 数据库，然后启动本地管理界面。

默认访问地址：

```text
http://127.0.0.1:8000
```

如果端口被占用：

```bat
set ANP_REVIEW_PORT=18000
start_anp.bat
```

也可以直接启动 Web 服务：

```bash
python -m review_queue.human_review
python -m review_queue.human_review --port 18000
```

## 2. 本地开发环境

建议使用 Python 3.11+。

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

没有真实模型 API key 时，ANP 会走 mock / dry-run 路径，方便在本地验证流程、界面和数据库写入。

## 3. 配置

默认读取 `config.yaml`，也可以用 `ANP_CONFIG` 指向私有配置文件：

```bash
set ANP_CONFIG=D:\secure\anp-config.yaml
```

敏感字段建议来自私有配置文件或环境变量，不要写入仓库：

```bash
set DEEPSEEK_API_KEY=your_api_key
```

常用配置项：

```yaml
deepseek:
  api_key: ""
  mock: true
runtime:
  dry_run: true
database:
  sqlite_path: "data/anp.sqlite3"
  backup_dir: "data/backups"
logging:
  file: "logs/anp.log"
```

常用环境变量包括：

- `ANP_REVIEW_PORT`：本地 Web 服务端口。
- `ANP_CONFIG`：配置文件路径。
- `ANP_SQLITE_PATH`：SQLite 数据库路径。
- `ANP_DRY_RUN`：强制 dry-run。
- `ANP_MOCK_DEEPSEEK`：强制 mock 模型调用。
- `ANP_AI_REVIEW_THRESHOLD`：AI 审查通过阈值。
- `ANP_MAX_REWRITE_ATTEMPTS`：自动重写次数上限。
- `ANP_MONTHLY_BUDGET_CNY`：月度预算上限。
- `ANP_DAILY_TOKEN_LIMIT`：每日 token 上限。

## 4. 功能流程

### 短篇小说

短篇小说流程从题材库开始，经过选题、故事框架、大纲、分节生成、精修、去 AI 味和分章标题等阶段，最终进入审核与人工复核。

命令行生成单篇：

```bash
python -m cli.generate --theme 雨夜归人 --word-count 3000 --style 现实温情
```

批量生成：

```bash
python -m cli.batch_generate --count 5 --theme 海边旧书店 --word-count 1200 --style 悬疑温情 --dry-run --print-ids
```

### 长篇小说

长篇小说流程覆盖题材定位、世界观、角色、势力、关系、全书大纲、卷纲、章节细纲和正文生成。正文工作流保留每章的草稿、扩写判断、润色、去 AI 味、审查、按建议修改、连续性检查和成稿文件。

在本地界面中进入“长篇小说”即可创建项目、生成设定、查看大纲、管理章节进度和运行正文流水线。

### 审核与管理

ANP Local Studio 包含：

- 监控数据：本地任务、成本、token、并发和错误概览。
- 短篇小说：题材选择、生成任务、作品列表、提示词编辑和阶段进度。
- 长篇小说：长篇项目、设定、大纲、正文、追踪记忆和生成链路。
- 题材库：短篇/长篇题材数据、来源和筛选。
- 日志：系统日志与 API 调用消耗。
- 设置：模型供应商、API key、Base URL、模型识别、预算和运行参数。

AI 审查可通过命令行运行：

```bash
python -m cli.ai_review --limit 20
python -m cli.ai_review --limit 20 --threshold 85
```

## 5. 统一入口

`main.py` 支持初始化配置、初始化数据库和手动备份 SQLite：

```bash
python main.py
python main.py --backup-now
```

SQLite 备份写入 `database.backup_dir`。

## 6. 本地验证

推荐提交前运行：

```bash
pytest -q
python -m py_compile config_loader.py main.py runtime_helpers.py generator\*.py review_queue\*.py cli\*.py
```

短篇 smoke test：

```bash
set ANP_SQLITE_PATH=data/local_verify.sqlite3
python -m cli.batch_generate --count 3 --theme 本地验收 --word-count 600 --dry-run --print-ids
python -m cli.ai_review --limit 5
```

## 7. 安全说明

- 不提交 `.env`、真实账号密码、API key、本地数据库、日志或浏览器数据。
- 日志只记录动作、状态、耗时、story_id 和错误摘要，不记录密钥。
- 生产或私有配置应放在仓库外，或通过环境变量注入。
- 项目不内置第三方账号托管、验证码处理、网页登录态维护或外部检测流程。
- 提交前建议运行 secret 扫描，确认没有敏感信息进入 Git 历史。

## 8. 常见问题

**没有 API key 会失败吗？**
不会。配置加载器会启用 mock / dry-run，生成和审核流程仍可本地验证。

**8000 端口被占用怎么办？**
使用 `python -m review_queue.human_review --port 18000`，或设置 `ANP_REVIEW_PORT=18000`。

**数据保存在哪里？**
默认保存到 `data/anp.sqlite3`。`data/` 是本地运行数据目录，不应提交到 GitHub。

**短篇和长篇可以同时使用吗？**
可以。两套流程共享本地配置、模型调用、预算控制和日志系统，但作品数据按类型分开管理。

## 9. Git 检查

提交前建议确认工作区和最近提交：

```bash
git status --short
git log --oneline -1
```
