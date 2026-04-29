# ANP 全自动小说创作与发布流水线 - 制作清单

## 核心目标

本地运行的自动化流水线：调用 DeepSeek API 或 mock 生成短篇小说 → 写入 SQLite 队列 → 人工/AI 审核 → dry-run 或安全暂停式发布到番茄小说。全自动模式串联生成、AI 审核、随机延迟发布和备份；半自动模式由人工审核页面介入。所有数据优先本地存储。

> 项目位置：`D:\Development_alma\anp`  
> 当前状态：✅ Sprint 7 最终交付打包完成  
> 范围声明：本轮完成本地 MVP 与番茄小说单平台适配；外部通知、多平台、统计面板为后续范围。

---

## 0. 环境准备与目录创建

- [x] 创建项目根目录 `D:\Development_alma\anp`
- [x] 在该目录下初始化 Git 仓库
- [ ] 创建虚拟环境（需用户按本机习惯执行，README 已提供命令）
- [ ] 安装基础依赖（需用户在目标机器执行 `pip install -r requirements.txt`）
- [ ] 验证 DeepSeek API key 可用（未提供真实 key；当前通过 mock/dry-run 验收）

---

## 1. Phase 1 – 生成 + 队列

- [x] **1.1 配置文件模板** `config.yaml`
- [x] **1.2 数据库模块** `queue/db.py`
- [x] **1.3 DeepSeek API 客户端** `generator/api_client.py`
- [x] **1.4 短篇小说提示词构建** `generator/prompt_builder.py`
- [x] **1.5 手动触发生成脚本** `cli/generate.py`
- [x] **验证**：运行生成 → 数据库中有一条 `status='pending'` 的记录
  - Sprint 2 命令：`ANP_SQLITE_PATH=data/sprint2_verify.sqlite3 python -m cli.generate --theme 海边旧书店 --word-count 800 --style 悬疑温情`
  - Sprint 7 命令：`set ANP_SQLITE_PATH=data/sprint7_verify.sqlite3 && python -m cli.generate --theme Sprint7单篇 --word-count 600 --style 验收 --print-content`
  - 结果：命令退出码 0，输出 `Generated story ID` 和 `Status: pending`。

---

## 2. Phase 2 – 审核模块

### 2.1 人工审核 Web 界面

- [x] **2.1.1** `queue/human_review.py` FastAPI 应用
- [x] **2.1.2** 启动命令 `python -m queue.human_review`
- [x] **2.1.3** 通过浏览器或 TestClient 访问 `http://localhost:8000` 测试
  - 验证方式：`pytest -q` 覆盖首页、批准、拒绝、编辑等路径。

### 2.2 AI 自动审核（打分 + 重写）

- [x] **2.2.1** `queue/ai_review.py` 实现评分函数（7 个维度）
- [x] **2.2.2** 调用 DeepSeek 进行评分输出 JSON（无 key 时 mock）
- [x] **2.2.3** 重写逻辑：最多 3 次
- [x] **2.2.4** 超过重试次数标记 `status='needs_human'`
- [x] **2.2.5** Web 界面增加「运行 AI 审核批次」按钮
- [x] **验证**：
  - 命令：`set ANP_SQLITE_PATH=data/sprint7_verify.sqlite3 && python -m cli.ai_review --limit 5`
  - 结果：`reviewed=1; approved=1; needs_human=0; failed=0; failure_reasons=none`，退出码 0。

---

## 3. Phase 3 – 番茄小说自动发布

- [x] **3.1 平台工作流文档** `docs/fansq_workflow.md`
- [x] **3.2 基类** `publisher/base_publisher.py`
- [x] **3.3 番茄适配器** `publisher/fansq.py`
- [x] **3.4 发布脚本** `cli/publish.py`
- [x] **3.5 验证码/滑块/登录态缺失处理**：暂停 + 截图 + 日志 + CLI 通知；禁止绕过
- [x] **验证**：
  - 命令：`set ANP_SQLITE_PATH=data/sprint7_verify.sqlite3 && python -m cli.publish --dry-run --dry-run-outcome success`
  - 结果：输出 `platform=fansq status=published status_preserved`，退出码 0。

---

## 4. Phase 4 – 模式切换与全自动调度

- [x] **4.1 完善 `config.yaml`**（调度时间、阈值、重试次数、成本上限）
- [x] **4.2 调度器** `scheduler.py`（APScheduler）
- [x] **4.3 统一启动入口** `main.py`
- [x] **4.4 日志与监控**（logging + Web 界面查看）
- [x] **验证**：`python main.py --mode auto --dry-run` 可在无外部密钥/登录态下完成本地端到端模拟；Sprint 6 已记录 `pytest -q` 通过。

---

## 5. Phase 5 – 增强功能

- [ ] **5.1 通知集成**（Telegram / 飞书）— 后续范围；当前只做 CLI 输出 + 日志记录
- [x] **5.2 批量生成** `cli/batch_generate.py --count N`
  - 命令：`set ANP_SQLITE_PATH=data/sprint7_batch_dryrun.sqlite3 && python -m cli.batch_generate --count 3 --theme Sprint7批量 --word-count 600 --dry-run --print-ids`
  - 结果：`requested=3 success=3 failed=0 dry_run=True`，SQLite 新增 3 条 `pending`。
- [ ] **5.3 多平台支持**（起点、公众号）— 后续范围；当前只支持 `fansq`
- [ ] **5.4 数据统计面板** — 后续范围

---

## 6. 风险应对与验收标准

- [x] API 费用超支（config 设置月限额、日 token 限额）
- [x] 番茄封号风险（发布间隔随机 5-15 分钟，遇风控暂停）
- [x] AI 审核误判（阈值可调，人工可复查）
- [x] 本地数据丢失（SQLite 备份）
- [x] 验证码/滑块/登录态缺失（暂停、截图、日志、CLI 通知，禁止绕过）

---

## 7. 最终交付物检查表

- [x] 项目完整源代码位于 `D:\Development_alma\anp`
- [x] `README.md` 覆盖安装、配置、生成、批量生成、审核、发布、调度、dry-run、FAQ、人工配置项与安全说明
- [x] `docs/fansq_workflow.md` 与 README 对验证码/滑块/登录态缺失策略一致，明确禁止绕过
- [x] `cli/batch_generate.py --count N` 可批量生成 N 篇并写入队列，支持 mock/dry-run，输出成功/失败数量
- [x] 能够通过 `python main.py --mode auto` 启动调度模式
- [x] 能够通过 `python main.py --mode semi-auto` 启动半自动模式
- [x] dry-run 生成、AI 审核、发布均已执行并记录命令/结果
- [x] 语法检查已执行：`python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py publisher/*.py cli/*.py`，退出码 0
- [x] 测试已执行：`pytest -q`，结果 `25 passed`
- [x] 所有 API key / 密码仅从 `config.yaml` 或环境变量读取
- [x] 最终阶段 commit 已完成：`Complete sprint 7 delivery package`

---

## 8. 未完成项、需人工配置项、阻塞原因

### 未完成 / 后续范围

- Telegram / 飞书外部通知未实现；当前以 CLI 输出、日志和截图作为本地通知/证据。
- 起点、公众号等多平台发布未实现；当前 `cli.publish` 仅支持 `fansq`。
- 数据统计面板未实现。
- 真实番茄最终提交按钮不做无人值守点击，建议人工复核分类、章节和敏感词后提交。

### 需人工配置项

- 如需真实生成/审核：配置 `DEEPSEEK_API_KEY` 或 `deepseek.api_key`。
- 如需真实发布：人工执行 Playwright codegen 保存 `data/browser/fansq_state.json`，并确认账号可进入作者发布页。
- 按目标机器创建虚拟环境、安装依赖和 Playwright 浏览器运行时。
- 按业务需要调整 `scheduler.*_cron`、发布间隔、AI 审核阈值、成本上限、数据库/备份/日志路径。

### 当前阻塞原因

- 无真实 DeepSeek key 与番茄登录态，因此验收使用 mock/dry-run；这不阻塞本地 MVP 交付。
- 外部通知、多平台、统计面板不属于 Sprint 7 必交范围，已明确标记后续。
