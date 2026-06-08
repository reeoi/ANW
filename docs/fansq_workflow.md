# 番茄小说发布工作流（Sprint 7）

本文档描述 ANW 通过 Playwright 发布到番茄小说（Fanqie Novel）的本地 MVP 工作流、人工登录态准备、dry-run 验证、风险暂停策略和禁止绕过原则。策略与 README 的“番茄验证码/滑块/登录态缺失策略”保持一致。

## 1. 前置条件

1. 安装依赖：

   ```bash
   pip install -r requirements.txt
   python -m playwright install
   ```

2. 配置 `config.yaml` 或环境变量。平台入口、账号标识、登录态路径只从配置读取，源码不硬编码账号密码：

   ```yaml
   publisher:
     fansq:
       enabled: true
       username: ""              # 可选；也可用 FANSQ_USERNAME
       login_state_path: "data/browser/fansq_state.json"
       draft_url: "https://fanqienovel.com/"
       pause_on_risk_control: true
   logging:
     file: "logs/anw.log"
     screenshot_dir: "logs/screenshots"
   ```

3. 队列中必须已有 `status='approved'` 的作品。发布 CLI 只读取 approved 记录，不发布 pending、needs_human、rejected 或 failed 记录。

## 2. 人工登录 / 会话准备

番茄小说可能要求短信、扫码、验证码、滑块或其他安全验证。ANW 不保存明文密码，不自动登录，也不尝试绕过验证。推荐由人工先准备 Playwright storage state：

```bash
python -m playwright codegen https://fanqienovel.com/ --save-storage=data/browser/fansq_state.json
```

操作步骤：

1. 在打开的浏览器中人工完成登录。
2. 如出现验证码、滑块、扫码、短信等验证，必须由人工自行完成。
3. 登录成功并确认可进入作者/发布入口后关闭 codegen。
4. 确认 `data/browser/fansq_state.json` 存在，并且路径与 `publisher.fansq.login_state_path` 一致。

`data/browser/` 建议作为本机私有目录；不要提交、共享或贴出登录态文件。

## 3. 真实发布页面步骤

真实模式下，`publisher/fansq.py` 的流程是：

1. 从配置读取 `draft_url`、`login_state_path`、`username`、`pause_on_risk_control`。
2. 启动 Playwright Chromium。
3. 使用 `login_state_path` 加载浏览器上下文。
4. 打开 `draft_url`。
5. 检查页面文本和 URL 中的登录、验证码、滑块、安全验证、风控、异常访问等信号。
6. 尝试识别标题与正文编辑区域，并填入 approved 作品标题和正文。
7. 再次检查风险信号。
8. MVP 在填入草稿后安全暂停或返回结果，要求人工复核章节、分类、作品设置和最终提交按钮，避免页面改版时误点发布。

## 4. dry-run 验证

无需番茄登录态即可验证发布链路：

```bash
# 模拟发布成功；默认保留 approved 状态，便于重复测试
python -m cli.publish --dry-run --dry-run-outcome success

# 模拟验证码/滑块/登录态缺失导致安全暂停；默认也保留状态
python -m cli.publish --dry-run --dry-run-outcome paused

# 如需把 dry-run 暂停结果写入 SQLite
python -m cli.publish --dry-run --dry-run-outcome paused --commit-dry-run
```

状态说明：

- dry-run 默认不修改队列状态，输出 `status_preserved`。
- `--commit-dry-run` 会将模拟结果写入 `published`、`publish_paused` 或 `publish_failed`。
- 真实模式会按结果更新状态；登录态缺失或风控暂停写入 `publish_paused`。

## 5. 验证码 / 滑块 / 登录态缺失处理原则

遇到以下情况必须暂停发布：

- 登录态缺失或过期。
- 页面要求登录、扫码、短信验证。
- 出现验证码、图形验证码、滑块、拖动验证。
- 出现安全验证、异常访问、风险提示、风控页。
- 页面跳转到非预期入口。
- 未识别到发布编辑器，疑似页面改版或账号权限异常。
- Playwright 运行异常或页面超时。

暂停时系统必须：

1. 立即停止后续自动点击、填充或提交。
2. 保存截图或占位证据到 `logs/screenshots`（或配置的 `logging.screenshot_dir`）。
3. 写入 `logs/anw.log`（或配置的 `logging.file`），包含平台、story_id、原因、截图路径。
4. 在 CLI 输出通知，说明需要人工处理；当前 MVP 未接入 Telegram/飞书等外部通知，外部通知列为后续范围。
5. 保持队列为 `publish_paused`（真实模式）或在 dry-run 默认保留状态，等待人工完成登录/验证/页面复核后再重试。

明确禁止：

- 不破解验证码。
- 不调用第三方打码平台。
- 不模拟拖动滑块或绕过滑块。
- 不绕过登录态、风控页或平台安全策略。
- 不为了自动提交而忽略异常跳转或页面改版信号。

## 6. 已知风险

- 番茄页面结构可能变化，选择器失效时会暂停。
- 登录态可能过期，需要人工重新保存 storage state。
- 账号权限、作品分类、章节规则、敏感词策略可能要求人工复核。
- 自动发布可能触发平台风控；应控制发布频率并优先人工确认。
- storage state 属于敏感文件，不要提交或共享。

## 7. 排障位置

- 日志：`logs/anw.log`
- 截图：`logs/screenshots/`
- 配置：`config.yaml` 或 `ANW_CONFIG`
- 队列状态：SQLite `stories.status`

如果截图显示验证码、滑块或登录页，人工处理后重新运行发布命令；不要修改代码去绕过风控。
