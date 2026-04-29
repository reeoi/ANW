# ANP 全自动小说创作与发布流水线

ANP 是本地运行的小说生成、审核、发布流水线。本仓库当前完成到 Sprint 3：可生成小说进入 SQLite 队列，并通过本地 FastAPI 人工审核页面批准、拒绝、编辑或触发 dry-run AI 审核批次。

## 本地安装

```bash
cd D:\Development_alma\anp
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

如需后续使用 Playwright 浏览器自动化：

```bash
python -m playwright install
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

# dry-run 发布检查
python -m cli.publish

# 启动人工审核页面（默认 http://localhost:8000）
python -m queue.human_review

# 如果 8000 端口已被其他本地服务占用，可仅改绑定端口；默认仍是 8000
python -m queue.human_review --port 18000

# 语法检查
python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py publisher/*.py cli/*.py
```

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
- 发布自动化后续只做正常浏览器操作，遇到验证码、滑块、登录态缺失或风控页面必须暂停、截图、记录日志，不绕过。

## Git 状态

Sprint 1 已完成阶段性提交；若 evaluator 环境看不到提交，请运行 `git log --oneline -1` 确认。
