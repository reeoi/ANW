# ANP 全自动小说创作与发布流水线 - 制作清单

## 核心目标
本地运行的自动化流水线，调用DeepSeek API生成短篇小说 → 可选人工/AI审核 → 自动发布到番茄小说。全自动模式完全无人干预（AI打分+自动重写），半自动模式人工介入审核队列。所有数据本地存储。

## 项目结构
anp/
├── config.yaml                 # API key、模式、平台账号、调度设置
├── generator/
│   ├── __init__.py
│   ├── api_client.py           # DeepSeek API封装
│   └── prompt_builder.py       # 短篇小说专用prompt（主题+字数+风格）
├── queue/
│   ├── db.py                   # SQLite表结构
│   ├── models.py               # 小说数据类
│   ├── human_review.py         # FastAPI人工审核界面
│   └── ai_review.py            # AI打分+问题列表+重写逻辑
├── publisher/
│   ├── base_publisher.py       # Playwright基类
│   └── fansq.py                # 番茄小说适配器
├── scheduler.py                # 定时任务
├── main.py                     # 启动入口
└── requirements.txt


> 项目位置：`D:\Development_alma\anp`  
> 当前状态：✅ 计划阶段 | 开始执行后勾选已完成项  
> 更新原则：每完成一个子任务，将 `[ ]` 改为 `[x]`，并如实记录耗时/问题

---

## 0. 环境准备与目录创建
- [x] 创建项目根目录 `D:\Development_alma\anp`
- [x] 在该目录下初始化 Git 仓库（可选）
- [ ] 创建虚拟环境
- [ ] 安装基础依赖
- [ ] 验证 DeepSeek API key 可用

---

## 1. Phase 1 – 生成 + 队列
- [x] **1.1 配置文件模板** `config.yaml`
- [x] **1.2 数据库模块** `queue/db.py`
- [x] **1.3 DeepSeek API 客户端** `generator/api_client.py`
- [x] **1.4 短篇小说提示词构建** `generator/prompt_builder.py`
- [x] **1.5 手动触发生成脚本** `cli/generate.py`
- [x] **验证**：运行生成 → 数据库中有一条 status='pending' 的记录
  - 实际命令：`ANP_SQLITE_PATH=data/sprint2_verify.sqlite3 python -m cli.generate --theme 海边旧书店 --word-count 800 --style 悬疑温情`
  - 结果：命令退出码 0，输出 `Generated story ID: 1`，SQLite `stories` 表新增记录且 `status='pending'`（2026-04-29 验证）。

---

## 2. Phase 2 – 审核模块
### 2.1 人工审核 Web 界面
- [x] **2.1.1** `queue/human_review.py` FastAPI 应用
- [x] **2.1.2** 启动命令 `python -m queue.human_review`
- [x] **2.1.3** 通过浏览器访问 `http://localhost:8000` 测试
  - 验证方式：使用本地 ASGI/TestClient 等价请求验证 `GET /` 可列出待审作品，`POST /stories/{id}/approve` 可更新 SQLite 状态；`pytest -q` 通过 7 项测试（2026-04-29）。

### 2.2 AI 自动审核（打分 + 重写）

- [x] **2.2.1** `queue/ai_review.py` 实现评分函数（7个维度）

- [x] **2.2.2** 调用 DeepSeek 进行评分输出 JSON

- [x] **2.2.3** 重写逻辑：最多3次

- [x] **2.2.4** 超过重试次数标记 `status='needs_human'`

- [x] **2.2.5** Web界面增加「运行 AI 审核批次」按钮（Sprint 3 已接入，Sprint 4 已接入完整七维评分、自动重写与转人工逻辑）

- 验证方式：`pytest -q tests/test_sprint4.py` 通过 5 项；`python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py publisher/*.py cli/*.py` 退出码 0（2026-04-29）。

- CLI 验证：`python -m cli.ai_review --limit 20` 可输出 `reviewed/approved/needs_human/failed/failure_reasons`。

---

## 3. Phase 3 – 番茄小说自动发布
- [x] **3.1 平台调研** `docs/fansq_workflow.md`
- [x] **3.2 基类** `publisher/base_publisher.py`
- [x] **3.3 番茄适配器** `publisher/fansq.py`
- [x] **3.4 发布脚本** `cli/publish.py`
- [x] **3.5 验证码/滑块处理**（暂停+日志通知）
  - Sprint 5 验证方式：`pytest -q tests/test_sprint5.py` 通过 5 项；`python -m py_compile config_loader.py main.py scheduler.py generator/*.py queue/*.py publisher/*.py cli/*.py` 退出码 0。dry-run 可模拟 success / paused，真实模式登录态缺失会保存截图并写日志安全暂停（2026-04-29）。


---

## 4. Phase 4 – 模式切换与全自动调度
- [ ] **4.1 完善 `config.yaml`**（调度时间、阈值、重试次数）
- [ ] **4.2 调度器** `scheduler.py`（APScheduler）
- [ ] **4.3 统一启动入口** `main.py`
- [ ] **4.4 日志与监控**（logging + Web界面查看）

---

## 5. Phase 5 – 增强功能
- [ ] **5.1 通知集成**（Telegram / 飞书）
- [ ] **5.2 批量生成** `cli/batch_generate.py --count 5`
- [ ] **5.3 多平台支持**（起点、公众号）
- [ ] **5.4 数据统计面板**

---

## 6. 风险应对与验收标准
- [ ] API费用超支（config设置月限额）
- [ ] 番茄封号（发布间隔随机5-15分钟）
- [ ] AI审核误判（阈值可调，人工可复查）
- [ ] 本地数据丢失（SQLite每日备份）

---

## 7. 最终交付物检查表
- [ ] 项目完整源代码位于 `D:\Development_alma\anp`
- [ ] 一份可执行的 `README.md`
- [ ] 能够通过 `python main.py --mode auto` 一键启动
- [ ] 能够通过 `python main.py --mode semi-auto` 启动
- [ ] 测试日志：至少成功生成3篇，1篇通过AI审核并自动发布
- [ ] 所有 API key / 密码仅从 `config.yaml` 或环境变量读取

---

**开始执行后，每完成一项就把 `- [ ]` 改成 `- [x]`，并 commit 一次。**  
遇到阻塞时在清单下方添加 `### 阻塞记录` 小节，贴出错误信息。
