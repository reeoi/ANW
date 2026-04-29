# ANP 全自动小说创作与发布流水线

ANP 是本地运行的小说生成、审核、发布流水线。本仓库当前完成 Sprint 1：项目骨架、配置安全边界、SQLite/日志/CLI 基础设施。

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

# 启动人工审核 FastAPI 骨架
python -m queue.human_review

# 语法检查
python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py publisher/*.py cli/*.py
```

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
