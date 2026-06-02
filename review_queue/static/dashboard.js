(() => {


  const $ = (id) => document.getElementById(id);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const toasts = $('toasts');


  // ---------- Theme Management ----------
  function applyTheme(theme) {
    theme = theme === 'dark' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', theme);
    document.body.classList.toggle('light', theme === 'light');
    localStorage.setItem('anp-theme', theme);
    updateThemeUI(theme);
  }

  function initTheme() {
    const savedTheme = localStorage.getItem('anp-theme') || 'light';
    applyTheme(savedTheme);
  }

  function updateThemeUI(theme) {
    const themeSelect = document.getElementById('theme-select');
    if (themeSelect) themeSelect.value = theme === 'dark' ? 'dark' : 'light';
    const themeToggle = document.getElementById('display-settings-toggle');
    if (themeToggle) {
      const isDark = theme === 'dark';
      themeToggle.textContent = isDark ? '浅色' : '深色';
      themeToggle.title = isDark ? '切换浅色模式' : '切换深色模式';
      themeToggle.setAttribute('aria-label', themeToggle.title);
      themeToggle.setAttribute('aria-pressed', isDark ? 'true' : 'false');
    }
  }

  function applyFontSize(size) {
    size = ['normal', 'large', 'xlarge'].indexOf(size) >= 0 ? size : 'normal';
    document.documentElement.setAttribute('data-font-size', size);
    document.body.classList.remove('font-normal', 'font-large', 'font-xlarge');
    document.body.classList.add('font-' + size);
    localStorage.setItem('anp-font-size', size);
    const sel = document.getElementById('font-size-select');
    if (sel) sel.value = size;
  }

  function applyDensity(density) {
    density = density === 'dense' ? 'dense' : 'default';
    document.body.classList.toggle('dense', density === 'dense');
    localStorage.setItem('anp-density', density);
    const sel = document.getElementById('density-select');
    if (sel) sel.value = density;
  }

  function initDisplaySettings() {
    var btn = document.getElementById('display-settings-toggle');
    applyFontSize('xlarge');
    applyDensity('default');
    if (!btn) return;
    updateThemeUI(document.documentElement.getAttribute('data-theme') || localStorage.getItem('anp-theme') || 'light');
    btn.addEventListener('click', function(ev) {
      ev.stopPropagation();
      var current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      applyTheme(current === 'dark' ? 'light' : 'dark');
    });
  }

  function initSidebarCollapse() {
    var sidebar = document.querySelector('.sidebar');
    var toggle = document.getElementById('sidebar-toggle');
    if (!sidebar || !toggle) return;
    var stored = localStorage.getItem('anp-sidebar-collapsed') === '1';
    if (stored) sidebar.classList.add('collapsed');
    toggle.setAttribute('aria-label', sidebar.classList.contains('collapsed') ? '展开侧边栏' : '收起侧边栏');
    toggle.addEventListener('click', function() {
      sidebar.classList.toggle('collapsed');
      localStorage.setItem('anp-sidebar-collapsed', sidebar.classList.contains('collapsed') ? '1' : '0');
      toggle.setAttribute('aria-label', sidebar.classList.contains('collapsed') ? '展开侧边栏' : '收起侧边栏');
    });
  }

  function showConfirmAsync(msg) {
    return new Promise(function(resolve) { showConfirm(msg, function() { resolve(true); }); });
  }
  // Inline confirm — replaces window.confirm() which can be suppressed in CDP mode
  function showConfirm(msg, onOk) {
    var ok = document.createElement('button');
    ok.textContent = '确定';
    ok.className = 'primary';
    var cancel = document.createElement('button');
    cancel.textContent = '取消';
    cancel.className = 'ghost';
    cancel.style.marginLeft = '0.5rem';
    var div = document.createElement('div');
    div.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(4,28,28,0.78);backdrop-filter:blur(10px);z-index:99999;display:flex;align-items:center;justify-content:center;animation:fadeIn 0.2s ease-out';
    var card = document.createElement('div');
    card.style.cssText = 'background:var(--panel);padding:2rem;border-radius:14px;max-width:400px;width:90%;text-align:center;box-shadow:0 30px 60px rgba(0,0,0,0.6),0 0 80px rgba(255,215,0,0.06);border:1px solid var(--primary-border);animation:modalIn 0.3s cubic-bezier(0.16,1,0.3,1)';
    card.innerHTML = '<div style="margin-bottom:1.5rem;font-size:3rem"></div>'
      + '<p style="margin:0 0 2rem 0;font-size:1.1rem;font-weight:600;color:var(--text);line-height:1.6">' + escapeHtml(msg) + '</p>';
    var btnGroup = document.createElement('div');
    btnGroup.style.cssText = 'display:flex;justify-content:center;gap:1rem';
    btnGroup.appendChild(ok);
    btnGroup.appendChild(cancel);
    card.appendChild(btnGroup);
    div.appendChild(card);
    function close() { document.body.removeChild(div); }
    ok.onclick = function() { close(); if (onOk) onOk(); };
    cancel.onclick = close;
    div.onclick = function(e) { if (e.target === div) close(); };
    document.body.appendChild(div);
    ok.focus();
  }

  function toast(message, kind = 'info', ttl = 4000) {
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    // Added SVG icons for modern look
    const icons = {
      success: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-right:8px"><path d="M20 6L9 17l-5-5"/></svg>',
      error: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-right:8px"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
      warn: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-right:8px"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
      info: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-right:8px"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
    };
    el.innerHTML = (icons[kind] || icons.info) + '<span>' + message + '</span>';
    toasts.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(-10px) scale(0.95)';
      el.style.transition = 'all .4s cubic-bezier(0.16, 1, 0.3, 1)';
    }, ttl - 400);
    setTimeout(() => el.remove(), ttl);
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function api(path, options = {}) {
    const init = Object.assign({ headers: {} }, options);
    if (init.body && !(init.body instanceof FormData) && typeof init.body !== 'string') {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(init.body);
    }
    const response = await fetch(path, init);
    let data = null;
    const text = await response.text();
    try { data = text ? JSON.parse(text) : null; } catch (_e) { data = { ok: false, message: text }; }
    if (!response.ok) {
      const message = (data && (data.detail || data.message)) || ('请求失败：HTTP ' + response.status);
      const error = new Error(message);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data || {};
  }

  function withBusy(button, label = '处理中…') {
    if (!button) return () => {};
    const original = button.textContent;
    button.disabled = true;
    button.dataset.original = original;
    button.textContent = label;
    return () => {
      button.disabled = false;
      button.textContent = original;
      delete button.dataset.original;
    };
  }

  let monitorTimer = null;
  let cardsTimer = null;
  let notifSource = null;
  let monitorChartMode = 'cost';
  let monitorLastDaily = [];

  async function loadCards() {
    // Cards refresh = full cockpit refresh (lightweight enough to call every 15s)
    return loadOverview();
  }

  function startCardsTimer() {
    if (cardsTimer) clearInterval(cardsTimer);
    cardsTimer = setInterval(() => {
      if ($('overview') && $('overview').classList.contains('active')) loadCards();
    }, 15000);
  }

  async function loadLogs() {
    const output = $('logs-output');
    try {
      const linesInput = $('logs-lines');
      const lines = linesInput ? Number(linesInput.value || 150) : 150;
      const data = await api('/api/logs?max_lines=' + encodeURIComponent(lines));
      const file = $('logs-file');
      if (file) file.textContent = data.log_file || '未知';
      if (output) output.textContent = (data.lines || []).join('\n') || '暂无日志。';
    } catch (err) {
      if (output) output.textContent = '加载失败：' + err.message;
      toast(err.message, 'error');
    }
  }

  function bindLogs() {
    const btn = $('btn-refresh-logs');
    if (btn) btn.addEventListener('click', loadLogs);
  }

  function logCostStat(label, value, sub) {
    return ''
      + '<div class="logs-cost-stat">'
      +   '<span class="logs-cost-label">' + escapeHtml(label) + '</span>'
      +   '<strong>' + escapeHtml(value) + '</strong>'
      +   '<em>' + escapeHtml(sub) + '</em>'
      + '</div>';
  }

  function friendlyPhaseLabel(phase) {
    const raw = String(phase || '').trim();
    if (!raw) return '未标记阶段';
    const normalized = raw
      .replace(/^phase[_-]?/i, 'phase ')
      .replace(/[_-]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase();
    const map = {
      'phase 0': '阶段 0 · 选题',
      'phase 1': '阶段 1 · 框架 / 简介',
      'phase 2': '阶段 2 · 大纲',
      'phase 3': '阶段 3 · 逐节',
      'phase 3 aggregate': '阶段 3 · 合并成稿',
      'phase 4': '阶段 4 · 精修',
      'phase 5': '阶段 5 · 去 AI 味',
      'phase 5 5': '阶段 5.5 · 朱雀检测',
      'phase 6': '阶段 6 · 审核',
      'phase 7': '阶段 7 · 发布',
      'chat': '通用调用',
      'long novel premise': '长篇 · 题材定位',
      'long novel world': '长篇 · 世界观',
      'long novel world detail': '长篇 · 世界观 · 分主题详写',
      'long novel characters': '长篇 · 角色设计',
      'long novel characters roster': '长篇 · 角色设计 · 清单',
      'long novel characters detail': '长篇 · 角色设计 · 角色档案',
      'long novel factions': '长篇 · 势力',
      'long novel factions roster': '长篇 · 势力 · 清单',
      'long novel factions detail': '长篇 · 势力 · 势力档案',
      'long novel relations': '长篇 · 关系',
      'long novel outline': '长篇 · 全书大纲',
      'long novel volume outline': '长篇 · 卷纲',
      'long novel chapter outlines': '长篇 · 章节细纲',
    };
    return map[normalized] || normalized.replace(/^phase /, '阶段 ');
  }

  function fmtLatency(seconds) {
    if (seconds == null || isNaN(seconds)) return '—';
    var value = Number(seconds);
    return (value < 10 ? value.toFixed(1) : Math.round(value)) + 's';
  }

  function latencyPill(label, seconds, tone) {
    return '<span class="logs-latency-pill ' + tone + '">' + escapeHtml(label + ' ' + fmtLatency(seconds)) + '</span>';
  }

  function renderLogCosts(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    const summary = data.summary || {};
    const meta = $('logs-cost-meta');
    const summaryEl = $('logs-cost-summary');
    const body = $('logs-cost-body');
    if (meta) meta.textContent = summary.window_label || ('最近 ' + items.length + ' 次调用');
    if (summaryEl) {
      summaryEl.innerHTML = [
        logCostStat('累计花费', fmtCurrency(summary.total_cost_cny || 0), summary.window_label || '最近调用窗口'),
        logCostStat('调用次数', fmtNum(summary.count || items.length), '单次 API 计费记录'),
        logCostStat('平均单次', fmtCurrency(summary.avg_cost_cny || 0), '当前窗口平均'),
        logCostStat('最高单次', fmtCurrency(summary.peak_cost_cny || 0), summary.latest_at ? ('最近更新 ' + relTime(summary.latest_at)) : '暂无最近更新时间'),
      ].join('');
    }
    if (!body) return;
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="6" class="logs-cost-empty">暂无 API 调用消耗记录</td></tr>';
      return;
    }
    body.innerHTML = items.map(function(row) {
      const usageLine = '输入 ' + fmtNum(row.input_tokens || 0)
        + ' · 缓存 ' + fmtNum(row.cached_tokens || 0)
        + ' · 输出 ' + fmtNum(row.output_tokens || 0);
      const time = formatLocalTime(row.occurred_at);
      const phase = friendlyPhaseLabel(row.phase);
      const workType = row.work_type === 'long_novel' ? '长篇'
        : row.work_type === 'short_story' ? '短篇'
        : '作品';
      const workLine = row.work_title
        ? (row.work_title + (row.work_id != null ? ' · ' + workType + ' #' + row.work_id : '')
          + (row.association_inferred ? ' · 历史推断' : ''))
        : (row.story_title
          ? (row.story_title + (row.story_id != null ? ' · 短篇 #' + row.story_id : ''))
          : '未绑定作品');
      return ''
        + '<tr>'
        +   '<td class="logs-cost-timecell"><strong>' + escapeHtml(time) + '</strong><span>' + escapeHtml(relTime(row.occurred_at)) + '</span></td>'
        +   '<td class="logs-cost-phasecell"><span class="badge amber">' + escapeHtml(phase) + '</span><em>' + escapeHtml(workLine) + '</em></td>'
        +   '<td class="logs-cost-modelcell"><strong>' + escapeHtml(row.model || '—') + '</strong></td>'
        +   '<td class="logs-cost-usagecell"><strong>' + escapeHtml(usageLine) + '</strong><span>按单次调用写入计费记录</span></td>'
        +   '<td class="logs-cost-latencycell">' + latencyPill('用时', row.duration_seconds, 'total') + latencyPill('首句', row.first_sentence_seconds, 'first-sentence') + '</td>'
        +   '<td class="logs-cost-pricecell"><strong>' + escapeHtml(fmtCurrency(row.cost_cny || 0)) + '</strong></td>'
        + '</tr>';
    }).join('');
  }

  async function loadLogs() {
    const output = $('logs-output');
    const file = $('logs-file');
    const costMeta = $('logs-cost-meta');
    const costSummary = $('logs-cost-summary');
    const costBody = $('logs-cost-body');
    try {
      const linesInput = $('logs-lines');
      const lines = linesInput ? Number(linesInput.value || 150) : 150;
      const [logRes, costRes] = await Promise.allSettled([
        api('/api/logs?max_lines=' + encodeURIComponent(lines)),
        api('/api/logs/costs?limit=80'),
      ]);
      if (logRes.status === 'fulfilled') {
        const data = logRes.value || {};
        if (file) file.textContent = data.log_file || '未知';
        if (output) output.textContent = (data.lines || []).join('\n') || '暂无日志。';
      } else {
        throw logRes.reason;
      }
      if (costRes.status === 'fulfilled') {
        renderLogCosts(costRes.value || {});
      } else {
        if (costMeta) costMeta.textContent = '调用消耗加载失败';
        if (costSummary) {
          costSummary.innerHTML = [
            logCostStat('累计花费', '—', '暂时无法读取数据库记录'),
            logCostStat('调用次数', '—', '刷新后重试'),
            logCostStat('平均单次', '—', '接口返回失败'),
            logCostStat('最高单次', '—', '接口返回失败'),
          ].join('');
        }
        if (costBody) {
          costBody.innerHTML = '<tr><td colspan="6" class="logs-cost-empty">API 调用消耗加载失败</td></tr>';
        }
      }
    } catch (err) {
      if (output) output.textContent = '加载失败：' + err.message;
      toast(err.message, 'error');
    }
  }

  async function loadMonitor() {
    const metaEl = $('monitor-meta');
    try {
      const [mon, conc] = await Promise.all([
        api('/api/monitor'),
        api('/api/monitor/concurrency').catch(() => ({ ok: false })),
      ]);
      monitorLastDaily = mon.daily_usage || [];
      renderMonitorKpis(mon);
      renderMonitorTrend(monitorLastDaily);
      renderMonitorLongNovel(mon.long_novel || {});
      renderMonitorQuota(mon, conc);
      renderMonitorEvents(mon.recent_events || []);
      renderMonitorHeatmap(mon.recent_errors || []);
      renderMonitorCompass(mon, conc);
      if (metaEl) {
        metaEl.textContent = '更新于 ' + formatLocalTime(mon.generated_at) + (mon.dry_run ? '  ·  Dry-run' : '  ·  Live');
      }
      const qmeta = $('monitor-quota-meta');
      if (qmeta) qmeta.textContent = '本月预算 / 24h Token / 并发槽';
      const evMeta = $('monitor-events-meta');
      if (evMeta) evMeta.textContent = (mon.recent_events || []).filter(function(ev) { return ev.kind !== 'publish'; }).length + ' 条';
      const healthMeta = $('monitor-health-meta');
      if (healthMeta) healthMeta.textContent = (mon.health || {}).db_path ? '数据库就绪' : '检测中';
    } catch (err) {
      const kpis = $('monitor-kpis');
      if (kpis) kpis.innerHTML = '<div class="empty">加载失败：' + escapeHtml(err.message) + '</div>';
      if (metaEl) metaEl.textContent = '加载失败';
      toast(err.message, 'error');
    }
  }

  function bindMonitor() {
    const btn = $('btn-monitor-refresh');
    if (btn) btn.addEventListener('click', loadMonitor);
    const exportBtn = $('btn-monitor-export-csv');
    if (exportBtn) exportBtn.addEventListener('click', () => {
      const rows = [['day', 'cost_cny', 'tokens', 'calls']].concat((monitorLastDaily || []).map((d) => [
        d.day || '',
        String(d.cost || 0),
        String(d.tokens || 0),
        String(d.calls || 0),
      ]));
      const csv = rows.map((row) => row.map((cell) => '"' + String(cell).replace(/"/g, '""') + '"').join(',')).join('\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'monitor-' + new Date().toISOString().slice(0, 10) + '.csv';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    });
    const refresh = $('monitor-refresh');
    if (refresh) refresh.addEventListener('change', startMonitorTimer);
    $$('[data-monitor-chart]').forEach((tab) => {
      tab.addEventListener('click', () => {
        monitorChartMode = tab.dataset.monitorChart || 'cost';
        $$('[data-monitor-chart]').forEach((el) => el.classList.toggle('active', el === tab));
        renderMonitorTrend(monitorLastDaily);
      });
    });
  }

  function startMonitorTimer() {
    if (monitorTimer) { clearInterval(monitorTimer); monitorTimer = null; }
    const refresh = $('monitor-refresh');
    const seconds = refresh ? Number(refresh.value || 0) : 0;
    if (seconds > 0) {
      monitorTimer = setInterval(() => {
        if ($('monitor') && $('monitor').classList.contains('active')) loadMonitor();
      }, seconds * 1000);
    }
  }

  function startNotificationStream() {
    // SSE is optional for interactivity; polling endpoints keep the UI usable.
  }

  function bindModeToggle() {}

  function setGenKeyVisible(visible) {
    const input = $('gen-key');
    if (!input) return;
    const btn = document.querySelector('[data-eye="gen-key"]');
    input.type = visible ? 'text' : 'password';
    if (btn) {
      btn.textContent = visible ? '隐藏' : '显示';
      btn.title = visible ? '隐藏 API Key' : '显示 API Key';
      btn.setAttribute('aria-label', visible ? '隐藏 API Key' : '显示 API Key');
      btn.setAttribute('aria-pressed', visible ? 'true' : 'false');
    }
  }

  async function loadGenerationSettings() {
    const meta = $('settings-meta');
    const msg = $('gen-test-msg');
    try {
      const data = await api('/api/settings/generation');
      const key = $('gen-key');
      const provider = $('gen-provider');
      const protocol = $('gen-protocol');
      const model = $('gen-model');
      const flashModel = $('gen-flash-model');
      const base = $('gen-base');
      const status = $('gen-key-status');
      if (key) key.value = '';
      setGenKeyVisible(false);
      if (provider) {
        provider.innerHTML = (data.providers || []).map(function(p) {
          return '<option value="' + escapeHtml(p.id) + '">' + escapeHtml(p.label) + '</option>';
        }).join('');
        provider.dataset.presets = JSON.stringify(data.providers || []);
        provider.value = data.provider || 'deepseek';
      }
      if (protocol) protocol.value = data.protocol || 'openai';
      if (model) model.value = data.model || '';
      if (flashModel) flashModel.value = data.flash_model || '';
      if (base) base.value = data.base_url || '';
      if (status) status.textContent = data.has_api_key ? '已配置' : '';
      if (msg) msg.textContent = '';
      if (meta) meta.textContent = '设置面板已加载。';
    } catch (err) {
      if (meta) meta.textContent = '加载失败：' + err.message;
      toast(err.message, 'error');
    }
  }

  async function saveGenerationSettings(button) {
    const done = withBusy(button, '保存中…');
    const msg = $('gen-test-msg');
    try {
      const payload = {
        provider: (($('gen-provider') || {}).value || 'deepseek').trim(),
        protocol: (($('gen-protocol') || {}).value || 'openai').trim(),
        model: (($('gen-model') || {}).value || '').trim(),
        flash_model: (($('gen-flash-model') || {}).value || '').trim(),
        base_url: (($('gen-base') || {}).value || '').trim(),
      };
      const keyValue = (($('gen-key') || {}).value || '').trim();
      if (keyValue) payload.api_key = keyValue;
      const data = await api('/api/settings/generation', { method: 'POST', body: payload });
      if ($('gen-key')) $('gen-key').value = '';
      const status = $('gen-key-status');
      if (status && (keyValue || status.textContent)) status.textContent = '已配置';
      if (msg) msg.textContent = data.message || '已保存';
      toast(data.message || '生成配置已保存', 'success');
    } catch (err) {
      if (msg) msg.textContent = '保存失败：' + err.message;
      toast(err.message, 'error');
    } finally {
      done();
    }
  }

  async function testGenerationSettings(button) {
    const done = withBusy(button, '测试中…');
    const msg = $('gen-test-msg');
    try {
      const payload = {
        provider: (($('gen-provider') || {}).value || 'deepseek').trim(),
        protocol: (($('gen-protocol') || {}).value || 'openai').trim(),
        model: (($('gen-model') || {}).value || '').trim(),
        base_url: (($('gen-base') || {}).value || '').trim(),
      };
      const keyValue = (($('gen-key') || {}).value || '').trim();
      if (keyValue) payload.api_key = keyValue;
      const data = await api('/api/settings/generation/test', { method: 'POST', body: payload });
      if (msg) msg.textContent = data.message || (data.ok ? '连接成功' : '连接失败');
      toast(data.message || (data.ok ? '连接成功' : '连接失败'), data.ok ? 'success' : 'error');
    } catch (err) {
      if (msg) msg.textContent = '测试失败：' + err.message;
      toast(err.message, 'error');
    } finally {
      done();
    }
  }

  function bindSettings() {
    const provider = $('gen-provider');
    if (provider && provider.dataset.bound !== '1') {
      provider.dataset.bound = '1';
      provider.addEventListener('change', () => {
        let presets = [];
        try { presets = JSON.parse(provider.dataset.presets || '[]'); } catch (_e) {}
        const preset = presets.find((p) => p.id === provider.value) || {};
        const model = $('gen-model');
        const flashModel = $('gen-flash-model');
        const base = $('gen-base');
        const protocol = $('gen-protocol');
        if (protocol && preset.protocol) protocol.value = preset.protocol;
        if (base) base.value = preset.base_url || '';
        if (model) model.value = '';
        if (flashModel) flashModel.value = '';
      });
    }
    $$('[data-eye]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const input = $(btn.dataset.eye || '');
        if (!input) return;
        setGenKeyVisible(input.type === 'password');
        input.focus();
      });
    });
    $$('[data-save="generation"]').forEach((btn) => {
      btn.addEventListener('click', () => saveGenerationSettings(btn));
    });
    $$('[data-reset="generation"]').forEach((btn) => {
      btn.addEventListener('click', loadGenerationSettings);
    });
    const testBtn = $('gen-test');
    if (testBtn) testBtn.addEventListener('click', () => testGenerationSettings(testBtn));
  }

  function loadAllSettings() {
    loadGenerationSettings();
  }

  function showSection(target) {
    if (target === 'overview') target = 'monitor';
    if (!target) return;
    document.body.classList.toggle('monitor-dashboard-active', target === 'monitor');
    $$('.section').forEach((el) => el.classList.toggle('active', el.id === target));
    $$('#nav button').forEach((btn) => btn.classList.toggle('active', btn.dataset.target === target));
    // Stop all timers, start only relevant ones
    if (_consoleTimer) { clearInterval(_consoleTimer); _consoleTimer = null; }
    if (monitorTimer) { clearInterval(monitorTimer); monitorTimer = null; }
    if (cardsTimer) { clearInterval(cardsTimer); cardsTimer = null; }
    if (notifSource) { notifSource.close(); notifSource = null; }
    if (target === 'overview') { loadOverview(); loadCards(); startCardsTimer(); }
    if (target === 'monitor') { loadMonitor(); startMonitorTimer(); }
    if (target === 'generate') { loadInbox(); loadConsoleStatus(); }
    if (target === 'long-novel') { _lnActiveBookId = null; $('ln-library-view').style.display = ''; $('ln-book-workspace').style.display = 'none'; loadBookList(); }
    if (target === 'theme-pool') loadThemePoolPage();
    if (target === 'logs') loadLogs();
    if (target === 'settings-edit') loadAllSettings();
    if (target === 'overview' || target === 'monitor' || target === 'generate') startNotificationStream();
  }

  // ---------- Overview (Cockpit) ----------
  async function loadOverview() {
    const meta = $('overview-meta');
    try {
      const [dash, mon, cards, conc] = await Promise.all([
        api('/api/dashboard'),
        api('/api/monitor').catch(() => ({})),
        api('/api/monitor/cards').catch(() => ({})),
        api('/api/monitor/concurrency').catch(() => ({})),
      ]);
      let consoleStatus = null;
      try { consoleStatus = await api('/api/console/status'); } catch (e) { consoleStatus = null; }

      renderHeroDeck(dash, mon, cards, consoleStatus);
      renderPulseBar(dash, mon);
      renderOpsThroughput(mon, dash);
      renderOpsBurn(mon, cards);
      renderOpsLongNovel(dash.long_novel || {});
      renderOpsSlots(conc);
      renderOverviewTimeline(dash.recent || [], (dash.long_novel || {}).recent || []);
      renderOverviewAlerts(dash.warnings || [], mon, cards);

      if (meta) {
        meta.textContent = '数据库：' + (dash.database || '未知') + '  ·  ' +
          (dash.dry_run ? 'Dry-run 模式' : 'Live 模式') + '  ·  更新于 ' + formatLocalTime(mon.generated_at || new Date().toISOString());
      }
    } catch (err) {
      if (meta) meta.textContent = '加载失败：' + err.message;
      toast(err.message, 'error');
    }
  }

  // ============================================================
  //   Cockpit helpers: sparkline / ring / trend / heatmap / formats
  // ============================================================

  function formatLocalTime(iso) {
    if (!iso) return '—';
    try {
      const d = parseTs(iso);
      if (!d) return iso;
      const pad = (n) => String(n).padStart(2, '0');
      return pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    } catch (e) { return iso; }
  }

  function parseTs(s) {
    if (!s) return null;
    // SQLite TEXT timestamps are usually 'YYYY-MM-DD HH:MM:SS' (UTC) — make explicit
    let str = String(s);
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(str)) str = str.replace(' ', 'T') + 'Z';
    const d = new Date(str);
    return isNaN(d.getTime()) ? null : d;
  }

  function relTime(iso) {
    const d = parseTs(iso);
    if (!d) return iso || '—';
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return Math.max(0, Math.floor(diff)) + ' 秒前';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + ' 天前';
    return formatLocalTime(iso);
  }

  function fmtNum(n, digits = 0) {
    if (n == null || isNaN(n)) return '—';
    n = Number(n);
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (Math.abs(n) >= 1e4) return (n / 1e3).toFixed(1) + 'k';
    return digits ? n.toFixed(digits) : Math.round(n).toLocaleString();
  }

  function fmtCurrency(n) {
    if (n == null || isNaN(n)) return '¥ —';
    n = Number(n);
    if (Math.abs(n) >= 1000) return '¥ ' + n.toFixed(0);
    return '¥ ' + n.toFixed(2);
  }

  // Sparkline: values: number[], returns inline SVG string
  function sparklineSVG(values, color, opts) {
    opts = opts || {};
    const w = opts.width || 100;
    const h = opts.height || 24;
    const arr = Array.isArray(values) && values.length > 0 ? values : [0];
    const max = Math.max.apply(null, arr.concat([1]));
    const min = Math.min.apply(null, arr.concat([0]));
    const range = max - min || 1;
    const step = arr.length > 1 ? w / (arr.length - 1) : 0;
    const pts = arr.map((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      return x.toFixed(1) + ',' + y.toFixed(1);
    });
    const linePath = 'M' + pts.join(' L');
    const areaPath = linePath + ' L' + w + ',' + h + ' L0,' + h + ' Z';
    const stroke = color || 'var(--primary)';
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
      '<path d="' + areaPath + '" fill="' + stroke + '" opacity="0.18"/>' +
      '<path d="' + linePath + '" stroke="' + stroke + '" stroke-width="1.6" fill="none" stroke-linejoin="round" stroke-linecap="round"/>' +
      '</svg>';
  }

  // Ring gauge: percent (0-100), label center, sub center smaller
  function ringSVG(percent, opts) {
    opts = opts || {};
    const size = opts.size || 116;
    const stroke = opts.stroke || 9;
    const r = (size - stroke) / 2;
    const c = 2 * Math.PI * r;
    const pct = Math.max(0, Math.min(100, Number(percent) || 0));
    const dash = (pct / 100) * c;
    const color = opts.color || 'var(--primary)';
    const cx = size / 2;
    const label = opts.label != null ? String(opts.label) : (pct.toFixed(0) + '%');
    const sub = opts.sub != null ? String(opts.sub) : '';
    return '<svg class="ring-svg" viewBox="0 0 ' + size + ' ' + size + '" width="' + size + '" height="' + size + '">' +
      '<circle class="ring-track" cx="' + cx + '" cy="' + cx + '" r="' + r + '" fill="none" stroke-width="' + stroke + '"/>' +
      '<circle class="ring-bar" cx="' + cx + '" cy="' + cx + '" r="' + r + '" fill="none" stroke-width="' + stroke + '"' +
        ' stroke="' + color + '" stroke-linecap="round"' +
        ' stroke-dasharray="' + dash.toFixed(2) + ' ' + (c - dash).toFixed(2) + '"' +
        ' transform="rotate(-90 ' + cx + ' ' + cx + ')"/>' +
      '<text class="ring-label-num" x="' + cx + '" y="' + (cx + 4) + '" text-anchor="middle">' + escapeHtml(label) + '</text>' +
      (sub ? '<text class="ring-label-suffix" x="' + cx + '" y="' + (cx + 20) + '" text-anchor="middle">' + escapeHtml(sub) + '</text>' : '') +
      '</svg>';
  }

  function ringColorByLevel(level) {
    if (level === 'danger') return 'var(--danger)';
    if (level === 'warn') return 'var(--warning)';
    return 'var(--success)';
  }

  function pctLevel(pct) {
    if (pct >= 90) return 'danger';
    if (pct >= 60) return 'warn';
    return 'ok';
  }

  function longNovelProgressStats(longNovel) {
    longNovel = longNovel || {};
    const done = Number(longNovel.chapters_done || 0);
    const writing = Number(longNovel.chapters_writing || 0);
    const outlined = Number(longNovel.chapters_outline || 0);
    const total = Number(longNovel.chapters_total || 0);
    const planned = Math.max(
      total,
      Number(longNovel.chapters_planned || 0),
      Number(longNovel.target_chapters_total || 0),
      Number(longNovel.target_chapters || 0),
      done + writing + outlined
    );
    const remaining = Math.max(0, Number(longNovel.chapters_remaining || 0) || (planned - done - writing));
    const pct = planned > 0 ? Math.round((done / planned) * 1000) / 10 : 0;
    const writingPct = planned > 0 ? Math.max(0, Math.min(100, (writing / planned) * 100)) : 0;
    const donePct = planned > 0 ? Math.max(0, Math.min(100, (done / planned) * 100)) : 0;
    return { done, writing, outlined, total, planned, remaining, pct, donePct, writingPct };
  }

  // ============================================================
  //   Overview renderers
  // ============================================================

  function renderHeroDeck(dash, mon, cards, consoleStatus) {
    const usage = (mon && mon.usage) || {};
    const d1 = usage.d1 || {};
    const stats = (dash && dash.stats) || {};
    const limits = (mon && mon.limits) || {};
    const longNovel = (dash && dash.long_novel) || {};

    // Health
    const warnings = (dash && dash.warnings) || [];
    const errors24 = (mon && (mon.recent_errors || []).filter((e) => {
      const ts = parseTs(e.occurred_at); if (!ts) return false;
      return (Date.now() - ts.getTime()) < 86400 * 1000;
    })) || [];
    const consecFails = ((cards || {}).last_run || {}).consecutive_failures || 0;
    const budgetLevel = ((cards || {}).budget || {}).level || 'ok';
    let healthScore = 100;
    healthScore -= Math.min(40, errors24.length * 6);
    healthScore -= Math.min(30, consecFails * 10);
    if (budgetLevel === 'warn') healthScore -= 8;
    if (budgetLevel === 'danger') healthScore -= 15;
    healthScore -= Math.min(15, warnings.length * 5);
    healthScore = Math.max(0, healthScore);
    const healthLevel = healthScore >= 80 ? 'ok' : (healthScore >= 50 ? 'warn' : 'danger');
    const healthLabel = healthLevel === 'ok' ? '运行平稳' : (healthLevel === 'warn' ? '需关注' : '异常');
    const healthSub = errors24.length === 0 && consecFails === 0
      ? '24 小时无错误事件'
      : (errors24.length + ' 次错误 / 连续失败 ' + consecFails);

    const heroHealth = $('hero-health');
    if (heroHealth) {
      heroHealth.classList.remove('level-ok', 'level-warn', 'level-danger');
      heroHealth.classList.add('level-' + healthLevel);
    }
    const ringEl = $('hero-health-ring');
    if (ringEl) ringEl.innerHTML = ringSVG(healthScore, {
      size: 116, stroke: 10,
      color: ringColorByLevel(healthLevel),
      label: healthScore.toFixed(0),
      sub: 'HEALTH',
    });
    const lblEl = $('hero-health-label');
    if (lblEl) lblEl.textContent = healthLabel;
    const subEl = $('hero-health-sub');
    if (subEl) subEl.textContent = healthSub;

    // Badges
    const badgesEl = $('hero-badges');
    if (badgesEl) {
      const badges = [];
      badges.push('<span class="hero-badge ' + (dash.dry_run ? 'bdg-dry' : 'bdg-live') + '">' +
        (dash.dry_run ? 'Dry-run' : 'Live') + '</span>');
      if (mon && mon.model) badges.push('<span class="hero-badge">' + escapeHtml(mon.model) + '</span>');
      if (consoleStatus && consoleStatus.current_task) {
        const t = consoleStatus.current_task;
        badges.push('<span class="hero-badge bdg-mute">▶ 在跑 #' + escapeHtml(t.story_id || '?') + '</span>');
      }
      if ((mon.schedule || {}).enabled === false || !mon.schedule) {
        badges.push('<span class="hero-badge bdg-mute">手动模式</span>');
      }
      badgesEl.innerHTML = badges.join('');
    }

    // Hero beats
    const today = ((mon.events || {}).d1) || {};
    const todayCalls = d1.calls || 0;
    const todayCost = d1.cost_cny || 0;
    let publishedToday = 0;
    Object.keys(today).forEach((k) => {
      const m = today[k];
      Object.keys(m).forEach((st) => {
        if (st === 'success' || st === 'approved' || st === 'published') publishedToday += m[st];
      });
    });
    const beatToday = $('beat-today');
    if (beatToday) beatToday.textContent = publishedToday;
    const beatTodaySub = $('beat-today-sub');
    if (beatTodaySub) beatTodaySub.textContent = '调用 ' + fmtNum(todayCalls) + ' · ' + fmtCurrency(todayCost);

    const beatRunning = $('beat-running');
    const beatRunSub = $('beat-running-sub');
    if (consoleStatus && consoleStatus.current_task) {
      const t = consoleStatus.current_task;
      const phase = t.current_phase || t.phase || '运行中';
      if (beatRunning) {
        beatRunning.textContent = '#' + (t.story_id || '?');
        beatRunning.classList.add('beat-running');
        beatRunning.classList.remove('beat-idle');
      }
      if (beatRunSub) beatRunSub.textContent = phase;
    } else {
      if (beatRunning) {
        beatRunning.textContent = '空闲';
        beatRunning.classList.remove('beat-running');
        beatRunning.classList.add('beat-idle');
      }
      if (beatRunSub) beatRunSub.textContent = '等待任务';
    }

    const totalLib = (stats.total || 0) + (longNovel.books_total || 0);
    const beatLib = $('beat-library');
    if (beatLib) beatLib.textContent = totalLib;
    const beatLibSub = $('beat-library-sub');
    if (beatLibSub) {
      beatLibSub.textContent = '短篇 ' + (stats.total || 0) + ' · 长篇 ' + (longNovel.books_total || 0)
        + ' · 成稿 ' + (longNovel.chapters_done || 0) + '/' + (longNovel.chapters_total || 0);
    }

    const budgetUsed = limits.spent_30d_cny || ((cards.budget || {}).used_cny) || 0;
    const budgetLimit = limits.monthly_budget_cny || ((cards.budget || {}).limit_cny) || 0;
    const budgetPct = budgetLimit > 0 ? (budgetUsed / budgetLimit) * 100 : 0;
    const beatBudget = $('beat-budget');
    if (beatBudget) beatBudget.textContent = fmtCurrency(budgetUsed);
    const beatBudgetSub = $('beat-budget-sub');
    if (beatBudgetSub) {
      beatBudgetSub.textContent = budgetLimit > 0
        ? ('上限 ' + fmtCurrency(budgetLimit) + ' · ' + budgetPct.toFixed(1) + '%')
        : '未设定上限';
    }
  }

  function renderPulseBar(dash, mon) {
    const target = $('overview-pulse');
    if (!target) return;
    const stats = (dash && dash.stats) || {};
    const daily = (mon && mon.daily_usage) || [];
    // Spark from daily total calls if no per-status time series available
    const sparkCalls = daily.map((d) => d.calls || 0);
    const cells = [
      { label: '全部', value: stats.total || 0, tone: 'primary', spark: sparkCalls, color: 'var(--primary)' },
      { label: '待审', value: (stats.pending || 0) + (stats.needs_human || 0), tone: 'warn', spark: sparkCalls, color: 'var(--warning)' },
      { label: '已批准', value: stats.approved || 0, tone: 'ok', spark: sparkCalls, color: 'var(--success)' },
      { label: '已完成', value: stats.published || 0, tone: 'info', spark: sparkCalls, color: 'var(--info)' },
      { label: '已拒绝', value: stats.rejected || 0, tone: '', spark: sparkCalls, color: 'var(--text-muted)' },
      { label: '失败', value: stats.failed || 0, tone: (stats.failed > 0 ? 'danger' : ''), spark: sparkCalls, color: 'var(--danger)' },
    ];
    target.innerHTML = cells.map((c) => (
      '<div class="pulse-cell ' + (c.tone ? 'tone-' + c.tone : '') + '">' +
        '<div class="pulse-label">' + escapeHtml(c.label) + '</div>' +
        '<div class="pulse-value">' + escapeHtml(c.value) + '</div>' +
        '<div class="pulse-spark">' + sparklineSVG(c.spark.length ? c.spark : [0, 0], c.color, { height: 22 }) + '</div>' +
      '</div>'
    )).join('');
  }

  function renderOpsThroughput(mon, dash) {
    const target = $('ops-throughput-bars');
    const metaEl = $('ops-throughput-meta');
    if (!target) return;
    const ev = ((mon || {}).events || {}).d1 || {};
    const usage = ((mon || {}).usage || {}).d1 || {};
    let gen = 0, rev = 0, fail = 0;
    Object.keys(ev).forEach((kind) => {
      const m = ev[kind] || {};
      Object.keys(m).forEach((st) => {
        const v = m[st];
        if (kind === 'generate') gen += v;
        else if (kind === 'review') rev += v;
        if (st === 'failed' || st === 'error') fail += v;
      });
    });
    const peak = Math.max(gen, rev, 1);
    const rows = [
      { label: '生成', value: gen, tone: 'primary' },
      { label: '审核', value: rev, tone: 'info' },
    ];
    if (gen + rev === 0) {
      target.innerHTML = '<div class="empty" style="padding:1rem;text-align:center">24 小时内暂无事件</div>';
    } else {
      target.innerHTML = rows.map((r) => {
        const pct = (r.value / peak) * 100;
        return '<div class="ops-throughput-row tone-' + r.tone + '">' +
          '<span class="tp-label">' + r.label + '</span>' +
          '<span class="tp-track"><span class="tp-fill" style="width:' + pct.toFixed(1) + '%"></span></span>' +
          '<span class="tp-value">' + r.value + '</span>' +
        '</div>';
      }).join('');
    }
    if (metaEl) {
      const calls = usage.calls || 0;
      const cost = usage.cost_cny || 0;
      metaEl.innerHTML = 'API 调用 <strong>' + fmtNum(calls) + '</strong> · 花费 <strong>' + fmtCurrency(cost) + '</strong>' +
        (fail > 0 ? ' · <span style="color:var(--danger)">⚠ ' + fail + ' 次失败</span>' : '');
    }
  }

  function renderOpsBurn(mon, cards) {
    const burnRing = $('ops-burn-ring');
    const burnMeta = $('ops-burn-meta');
    const card = $('ops-burn');
    if (!burnRing || !burnMeta) return;
    const lim = (mon && mon.limits) || {};
    const budgetCard = (cards && cards.budget) || {};
    const used = lim.spent_30d_cny || budgetCard.used_cny || 0;
    const limit = lim.monthly_budget_cny || budgetCard.limit_cny || 0;
    const pct = limit > 0 ? (used / limit) * 100 : 0;
    const level = budgetCard.level || pctLevel(pct);
    if (card) {
      card.classList.remove('level-ok', 'level-warn', 'level-danger');
      card.classList.add('level-' + level);
    }
    burnRing.innerHTML = ringSVG(pct, {
      size: 116, stroke: 10,
      color: ringColorByLevel(level),
      label: pct > 0 ? pct.toFixed(0) + '%' : '—',
      sub: 'BURN',
    });
    // Burn rate per day
    const today = new Date();
    const dayOfMonth = today.getDate();
    const dailyAvg = dayOfMonth > 0 ? used / dayOfMonth : 0;
    const remain = Math.max(0, limit - used);
    const daysLeft = dailyAvg > 0 ? Math.floor(remain / dailyAvg) : null;
    burnMeta.innerHTML =
      '<div class="ops-ring-meta-row"><span class="label">已用</span><span class="value">' + fmtCurrency(used) + '</span></div>' +
      '<div class="ops-ring-meta-row"><span class="label">上限</span><span class="value">' + (limit > 0 ? fmtCurrency(limit) : '未设') + '</span></div>' +
      '<div class="ops-ring-meta-row"><span class="label">日均</span><span class="value">' + fmtCurrency(dailyAvg) + '</span></div>' +
      '<div class="ops-ring-meta-row"><span class="label">剩可撑</span><span class="value">' + (daysLeft != null && limit > 0 ? daysLeft + ' 天' : '—') + '</span></div>';
  }

  function renderOpsLongNovel(longNovel) {
    const card = $('ops-long-novel');
    const stateEl = $('ops-long-state');
    const metaEl = $('ops-long-meta');
    const eyebrow = $('ops-long-eyebrow');
    if (!card || !stateEl) return;
    longNovel = longNovel || {};
    const books = longNovel.books_total || 0;
    const stats = longNovelProgressStats(longNovel);
    const done = stats.done;
    const total = stats.planned;
    const writing = longNovel.chapters_writing || 0;
    const level = books === 0 ? 'warn' : (writing > 0 ? 'ok' : 'warn');
    card.classList.remove('level-ok', 'level-warn', 'level-danger');
    card.classList.add('level-' + level);
    stateEl.textContent = books + ' 本书';
    if (eyebrow) eyebrow.textContent = total ? ('完成 ' + (longNovel.progress_pct || 0) + '%') : '暂无章节';
    if (metaEl) {
      metaEl.innerHTML =
        '<div><div class="lm-label">章节</div><div class="lm-value">' + done + ' / ' + total + '</div></div>' +
        '<div><div class="lm-label">字数</div><div class="lm-value">' + fmtNum(longNovel.words_total || 0) + '</div></div>';
    }
    stateEl.textContent = books + ' 本书';
    if (eyebrow) eyebrow.textContent = stats.planned ? ('成稿 ' + stats.pct + '%') : '暂无章节';
    if (metaEl) {
      metaEl.innerHTML =
        '<div><div class="lm-label">章节</div><div class="lm-value">' + stats.done + ' / ' + stats.planned + '</div></div>' +
        '<div><div class="lm-label">写作中</div><div class="lm-value">' + stats.writing + '</div></div>' +
        '<div><div class="lm-label">字数</div><div class="lm-value">' + fmtNum(longNovel.words_total || 0) + '</div></div>';
    }
  }

  function renderOpsSlots(conc) {
    const grid = $('ops-slots-grid');
    const metaEl = $('ops-slots-meta');
    if (!grid) return;
    const max = (conc && conc.max_concurrent) || 0;
    const inUse = (conc && conc.in_use) || 0;
    const free = (conc && conc.available) || Math.max(0, max - inUse);
    if (max === 0) {
      grid.innerHTML = '<div class="empty" style="padding:0.5rem">暂无信号</div>';
      if (metaEl) metaEl.innerHTML = '';
      return;
    }
    const slots = [];
    for (let i = 0; i < max; i++) {
      const used = i < inUse;
      slots.push('<div class="ops-slot ' + (used ? 'used' : 'free') + '" title="' + (used ? '正在跑' : '空闲') + '">' + (used ? '▶' : '○') + '</div>');
    }
    grid.innerHTML = slots.join('');
    if (metaEl) metaEl.innerHTML = '在用 <strong>' + inUse + '</strong> / 总 <strong>' + max + '</strong> · 空闲 <strong>' + free + '</strong>';
  }

  function renderOverviewTimeline(stories, longBooks) {
    const target = $('overview-recent');
    const countEl = $('overview-recent-count');
    if (!target) return;
    stories = stories || [];
    longBooks = longBooks || [];
    if (!stories.length && !longBooks.length) {
      target.classList.add('empty');
      target.innerHTML = '当前没有作品。请到 "短篇小说" / "长篇小说" 创建一篇。';
      if (countEl) countEl.textContent = '';
      return;
    }
    target.classList.remove('empty');
    if (countEl) countEl.textContent = '短篇 ' + stories.length + ' · 长篇 ' + longBooks.length;
    const statusMap = {
      pending: { tone: 'warn', text: '待审' },
      needs_human: { tone: 'warn', text: '转人工' },
      approved: { tone: 'ok', text: '已批准' },
      published: { tone: 'primary', text: '已完成' },
      rejected: { tone: '', text: '已拒绝' },
      failed: { tone: 'danger', text: '失败' },
      publish_paused: { tone: 'warn', text: '已暂停' },
    };
    const rows = [];
    stories.forEach((s) => {
      const st = statusMap[s.status] || { tone: '', text: s.status || '—' };
      const updated = s.updated_at || s.created_at;
      const idAttr = 'data-detail="' + escapeHtml(s.id) + '"';
      rows.push('<div class="ov-tl-row" ' + idAttr + ' tabindex="0">' +
        '<span class="ov-tl-time">' + escapeHtml(relTime(updated)) + '</span>' +
        '<span class="ov-tl-title">' +
          '<span class="tl-id">#' + escapeHtml(s.id) + '</span>' +
          '<span class="tl-name">' + escapeHtml(s.title || '未命名') + '</span>' +
          (s.is_dry_run ? '<span class="dryrun-marker">演练</span>' : '') +
        '</span>' +
        '<span class="ov-tl-badge ' + (st.tone ? 'bdg-' + st.tone : '') + '">' + escapeHtml(st.text) + '</span>' +
      '</div>');
    });
    longBooks.forEach((b) => {
      const updated = b.updated_at || b.created_at;
      rows.push('<div class="ov-tl-row" data-ln-book="' + escapeHtml(b.id) + '" tabindex="0">' +
        '<span class="ov-tl-time">' + escapeHtml(relTime(updated)) + '</span>' +
        '<span class="ov-tl-title">' +
          '<span class="tl-id">长篇 #' + escapeHtml(b.id) + '</span>' +
          '<span class="tl-name">' + escapeHtml(b.title || '未命名') + '</span>' +
        '</span>' +
        '<span class="ov-tl-badge bdg-primary">' + escapeHtml((b.chapters_done || 0) + '/' + (b.target_chapters || b.chapters_total || 0) + ' 章') + '</span>' +
      '</div>');
    });
    target.innerHTML = rows.join('');
  }

  function renderOverviewAlerts(warnings, mon, cards) {
    const target = $('overview-warnings');
    const meta = $('overview-warnings-meta');
    if (!target) return;
    const items = [];
    (warnings || []).forEach((w) => items.push({ tone: 'warn', icon: '⚠', title: '系统警告', body: w }));
    const last = (cards || {}).last_run;
    if (last && last.consecutive_failures >= 2) {
      items.push({
        tone: 'danger', icon: '',
        title: '连续 ' + last.consecutive_failures + ' 次失败',
        body: '最近事件：' + (last.message || last.kind || '—'),
        meta: relTime(last.occurred_at),
      });
    }
    const budget = (cards || {}).budget;
    if (budget && (budget.level === 'warn' || budget.level === 'danger')) {
      items.push({
        tone: budget.level,
        icon: '',
        title: '预算使用 ' + (budget.percent || 0) + '%',
        body: '已用 ' + fmtCurrency(budget.used_cny) + ' / 上限 ' + fmtCurrency(budget.limit_cny),
      });
    }
    const errs = (mon && mon.recent_errors) || [];
    if (errs.length >= 3) {
      items.push({
        tone: 'warn', icon: '',
        title: '近期错误 ' + errs.length + ' 条',
        body: '可前往 "监控数据中心" 查看详情。',
      });
    }
    if (!items.length) {
      target.classList.add('alerts-empty');
      target.classList.remove('alerts-list');
      target.innerHTML = '✓ 系统平稳运行 · 暂无提醒';
      if (meta) meta.textContent = '0 条';
      return;
    }
    target.classList.remove('alerts-empty');
    target.classList.add('alerts-list');
    if (meta) meta.textContent = items.length + ' 条';
    target.innerHTML = items.map((it) => (
      '<div class="alert-row tone-' + it.tone + '">' +
        '<div class="alert-icon">' + it.icon + '</div>' +
        '<div class="alert-content">' +
          '<div class="alert-title">' + escapeHtml(it.title) + '</div>' +
          '<div class="alert-body">' + escapeHtml(it.body) + '</div>' +
          (it.meta ? '<div class="alert-meta">' + escapeHtml(it.meta) + '</div>' : '') +
        '</div>' +
      '</div>'
    )).join('');
  }

  // ============================================================
  //   Monitor renderers
  // ============================================================

  function renderMonitorKpis(mon) {
    const target = $('monitor-kpis');
    if (!target) return;
    const u = mon.usage || {};
    const d1 = u.d1 || {}; const d7 = u.d7 || {}; const d30 = u.d30 || {};
    const ln = mon.long_novel || {};
    const daily = mon.daily_usage || [];
    const sparkCost = daily.map((d) => d.cost || 0);
    const sparkTokens = daily.map((d) => d.tokens || 0);
    const sparkCalls = daily.map((d) => d.calls || 0);
    // Failures sparkline derived from events not available daily — approximate from recent_errors clustered per day
    const errs = mon.recent_errors || [];
    const today = new Date();
    const errPerDay = [];
    for (let i = 13; i >= 0; i--) {
      const d = new Date(today); d.setDate(d.getDate() - i);
      const tag = d.toISOString().slice(0, 10);
      const count = errs.filter((e) => (e.occurred_at || '').slice(0, 10) === tag).length;
      errPerDay.push(count);
    }

    const successRate = (d7.calls > 0) ? (1 - (d7.failures / d7.calls)) * 100 : 100;

    const tiles = [
      { tone: 'primary', win: '长篇', label: '书籍', value: fmtNum(ln.books_total || 0), unit: '本', spark: [ln.books_total || 0], color: 'var(--primary)' },
      { tone: 'success', win: '长篇', label: '成稿章节', value: fmtNum(ln.chapters_done || 0), unit: '章', spark: [ln.chapters_done || 0], color: 'var(--success)' },
      { tone: 'info', win: '长篇', label: '累计字数', value: fmtNum(ln.words_total || 0), unit: '', spark: [ln.words_total || 0], color: 'var(--info)' },
      { tone: 'primary', win: '24H', label: '调用次数', value: fmtNum(d1.calls), unit: '', spark: sparkCalls, color: 'var(--primary)' },
      { tone: 'info', win: '24H', label: 'Token', value: fmtNum(d1.total_tokens), unit: '', spark: sparkTokens, color: 'var(--info)' },
      { tone: 'success', win: '24H', label: '花费', value: fmtCurrency(d1.cost_cny), unit: '', spark: sparkCost, color: 'var(--success)' },
      { tone: (d1.failures > 0 ? 'danger' : 'success'), win: '24H', label: '失败', value: fmtNum(d1.failures), unit: '次', spark: errPerDay, color: 'var(--danger)' },
      { tone: 'primary', win: '7D', label: '调用次数', value: fmtNum(d7.calls), unit: '', spark: sparkCalls, color: 'var(--primary)' },
      { tone: 'info', win: '7D', label: 'Token', value: fmtNum(d7.total_tokens), unit: '', spark: sparkTokens, color: 'var(--info)' },
      { tone: 'success', win: '7D', label: '花费', value: fmtCurrency(d7.cost_cny), unit: '', spark: sparkCost, color: 'var(--success)' },
      { tone: (successRate >= 95 ? 'success' : (successRate >= 80 ? 'warn' : 'danger')), win: '7D', label: '成功率', value: successRate.toFixed(1) + '%', unit: '', spark: sparkCalls, color: 'var(--success)' },
    ];
    target.innerHTML = tiles.map((t) => (
      '<div class="kpi-tile tone-' + t.tone + '">' +
        '<div class="kpi-eyebrow"><span>' + escapeHtml(t.label) + '</span><span class="kpi-window">' + t.win + '</span></div>' +
        '<div class="kpi-value">' + escapeHtml(t.value) + (t.unit ? '<span class="kpi-unit">' + escapeHtml(t.unit) + '</span>' : '') + '</div>' +
        '<div class="kpi-spark">' + sparklineSVG(t.spark.length ? t.spark : [0, 0], t.color, { height: 28 }) + '</div>' +
      '</div>'
    )).join('');
  }

  function renderMonitorTrend(daily) {
    const target = $('monitor-daily');
    if (!target) return;
    if (!daily.length) {
      target.classList.add('empty');
      target.textContent = '暂无消耗数据';
      return;
    }
    target.classList.remove('empty');
    const W = 880, H = 280;
    const padL = 50, padR = 50, padT = 18, padB = 32;
    const innerW = W - padL - padR;
    const innerH = H - padT - padB;
    const n = daily.length;
    const costs = daily.map((d) => Number(d.cost) || 0);
    const tokens = daily.map((d) => Number(d.tokens) || 0);
    const calls = daily.map((d) => Number(d.calls) || 0);
    const maxCost = Math.max.apply(null, costs.concat([1]));
    const maxToken = Math.max.apply(null, tokens.concat([1]));
    const maxCalls = Math.max.apply(null, calls.concat([1]));
    const bw = innerW / n;
    const xFor = (i) => padL + i * bw + bw / 2;
    const yCost = (v) => padT + innerH - (v / maxCost) * innerH;
    const yToken = (v) => padT + innerH - (v / maxToken) * innerH;
    const yCalls = (v) => padT + innerH - (v / maxCalls) * innerH;
    // Grid lines (4)
    const grid = [];
    for (let i = 0; i <= 4; i++) {
      const y = padT + (innerH / 4) * i;
      grid.push('<line class="mt-grid" x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y + '"/>');
    }
    // Call bars
    const bars = calls.map((v, i) => {
      const x = padL + i * bw + bw * 0.18;
      const w = bw * 0.64;
      const yTop = yCalls(v);
      const h = padT + innerH - yTop;
      return '<rect class="mt-bar" x="' + x.toFixed(1) + '" y="' + yTop.toFixed(1) + '" width="' + w.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="2"><title>' +
        daily[i].day + '\n调用: ' + v + '\nToken: ' + fmtNum(tokens[i]) + '\n花费: ' + fmtCurrency(costs[i]) + '</title></rect>';
    }).join('');
    // Cost line
    const costPath = 'M' + costs.map((v, i) => xFor(i).toFixed(1) + ',' + yCost(v).toFixed(1)).join(' L');
    // Token line
    const tokenPath = 'M' + tokens.map((v, i) => xFor(i).toFixed(1) + ',' + yToken(v).toFixed(1)).join(' L');
    // Dots
    const dotsCost = costs.map((v, i) => '<circle class="mt-dot-cost" cx="' + xFor(i).toFixed(1) + '" cy="' + yCost(v).toFixed(1) + '" r="3"/>').join('');
    const dotsTok = tokens.map((v, i) => '<circle class="mt-dot-tokens" cx="' + xFor(i).toFixed(1) + '" cy="' + yToken(v).toFixed(1) + '" r="2.5"/>').join('');
    // X labels (every other if too many)
    const everyN = n > 10 ? 2 : 1;
    const xLabels = daily.map((d, i) => {
      if (i % everyN !== 0) return '';
      const lbl = (d.day || '').slice(5);
      return '<text class="mt-axis-label" x="' + xFor(i).toFixed(1) + '" y="' + (H - 10) + '" text-anchor="middle">' + escapeHtml(lbl) + '</text>';
    }).join('');
    // Y left (cost)
    const yLeftLabels = [0, 0.5, 1].map((p) => {
      const y = padT + innerH * (1 - p);
      return '<text class="mt-axis-label" x="' + (padL - 8) + '" y="' + (y + 3) + '" text-anchor="end">' + (maxCost * p).toFixed(p === 0 ? 0 : 1) + '</text>';
    }).join('');
    // Y right (tokens)
    const yRightLabels = [0, 0.5, 1].map((p) => {
      const y = padT + innerH * (1 - p);
      return '<text class="mt-axis-label" x="' + (W - padR + 8) + '" y="' + (y + 3) + '">' + fmtNum(maxToken * p) + '</text>';
    }).join('');

    target.innerHTML =
      '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">' +
        grid.join('') +
        bars +
        '<path class="mt-line-cost" d="' + costPath + '"/>' +
        '<path class="mt-line-tokens" d="' + tokenPath + '"/>' +
        dotsCost + dotsTok +
        xLabels +
        yLeftLabels +
        yRightLabels +
      '</svg>';
  }

  function renderMonitorLongNovel(longNovel) {
    const target = $('monitor-long-novel');
    const meta = $('monitor-long-meta');
    if (!target) return;
    longNovel = longNovel || {};
    const books = longNovel.books_total || 0;
    if (meta) meta.textContent = books + ' 本书 · ' + (longNovel.chapters_done || 0) + '/' + (longNovel.chapters_total || 0) + ' 章';
    if (!books) {
      target.classList.add('empty');
      target.textContent = '暂无长篇小说数据';
      return;
    }
    target.classList.remove('empty');
    const recent = longNovel.recent || [];
    const cards = [
      { label: '书籍', value: books + ' 本', sub: '状态：' + Object.keys(longNovel.status || {}).map(function(k) { return k + ' ' + longNovel.status[k]; }).join(' · ') },
      { label: '章节进度', value: (longNovel.chapters_done || 0) + ' / ' + (longNovel.chapters_total || 0), sub: '写作中 ' + (longNovel.chapters_writing || 0) + ' · 待写 ' + (longNovel.chapters_outline || 0) },
      { label: '累计字数', value: fmtNum(longNovel.words_total || 0), sub: '整体完成 ' + (longNovel.progress_pct || 0) + '%' },
    ];
    target.innerHTML = '<div class="ln-monitor-summary">' + cards.map(function(c) {
      return '<div class="ln-monitor-card"><div class="lm-label">' + escapeHtml(c.label) + '</div><div class="lm-big">' + escapeHtml(c.value) + '</div><div class="lm-sub">' + escapeHtml(c.sub || '') + '</div></div>';
    }).join('') + '</div>'
      + '<div class="ln-monitor-recent">' + recent.map(function(b) {
        return '<button type="button" class="ln-monitor-book" data-ln-monitor-book="' + escapeHtml(b.id) + '">'
          + '<span class="lm-book-title">' + escapeHtml(b.title || '未命名') + '</span>'
          + '<span class="lm-book-meta">' + escapeHtml((b.chapters_done || 0) + '/' + (b.target_chapters || b.chapters_total || 0) + ' 章 · ' + fmtNum(b.words_total || 0) + '字') + '</span>'
          + '</button>';
      }).join('') + '</div>';
    target.querySelectorAll('[data-ln-monitor-book]').forEach(function(btn) {
      btn.addEventListener('click', function() { showSection('long-novel'); });
    });
  }

  function renderMonitorQuota(mon, conc) {
    const target = $('monitor-quota');
    if (!target) return;
    const lim = mon.limits || {};
    const cards = [];
    // Monthly budget
    const budgetPct = lim.monthly_budget_used_pct || 0;
    const budgetLevel = pctLevel(budgetPct);
    cards.push(
      '<div class="quota-card level-' + budgetLevel + '">' +
        '<div class="ring-wrap">' + ringSVG(budgetPct, { size: 90, stroke: 8, color: ringColorByLevel(budgetLevel), label: budgetPct.toFixed(0) + '%', sub: 'BUDGET' }) + '</div>' +
        '<div class="quota-info">' +
          '<div class="quota-label">月度预算</div>' +
          '<div class="quota-value">' + fmtCurrency(lim.spent_30d_cny) + '</div>' +
          '<div class="quota-sub">上限 ' + (lim.monthly_budget_cny > 0 ? fmtCurrency(lim.monthly_budget_cny) : '未设') + '</div>' +
        '</div>' +
      '</div>'
    );
    // Daily token
    const tokPct = lim.daily_token_used_pct || 0;
    const tokLevel = pctLevel(tokPct);
    cards.push(
      '<div class="quota-card level-' + tokLevel + '">' +
        '<div class="ring-wrap">' + ringSVG(tokPct, { size: 90, stroke: 8, color: ringColorByLevel(tokLevel), label: tokPct.toFixed(0) + '%', sub: 'TOKEN' }) + '</div>' +
        '<div class="quota-info">' +
          '<div class="quota-label">24h Token</div>' +
          '<div class="quota-value">' + fmtNum(lim.tokens_24h) + '</div>' +
          '<div class="quota-sub">上限 ' + (lim.daily_token_limit > 0 ? fmtNum(lim.daily_token_limit) : '未设') + '</div>' +
        '</div>' +
      '</div>'
    );
    // Concurrency
    const cMax = (conc && conc.max_concurrent) || 0;
    const cUse = (conc && conc.in_use) || 0;
    const cPct = cMax > 0 ? (cUse / cMax) * 100 : 0;
    const cLevel = cPct >= 100 ? 'warn' : 'ok';
    cards.push(
      '<div class="quota-card level-' + cLevel + '">' +
        '<div class="ring-wrap">' + ringSVG(cPct, { size: 90, stroke: 8, color: ringColorByLevel(cLevel), label: cUse + '/' + cMax, sub: 'SLOTS' }) + '</div>' +
        '<div class="quota-info">' +
          '<div class="quota-label">并发槽位</div>' +
          '<div class="quota-value">' + cUse + ' 在用</div>' +
          '<div class="quota-sub">总 ' + cMax + ' · 空闲 ' + Math.max(0, cMax - cUse) + '</div>' +
        '</div>' +
      '</div>'
    );
    target.classList.remove('empty');
    target.innerHTML = cards.join('');
  }

  function eventTone(status) {
    if (!status) return '';
    if (status === 'success' || status === 'approved' || status === 'published') return 'ok';
    if (status === 'failed' || status === 'error') return 'danger';
    if (status === 'paused' || status === 'warn' || status === 'warning') return 'warn';
    return 'info';
  }

  function eventIcon(kind, status) {
    if (status === 'failed' || status === 'error') return '✕';
    if (status === 'paused') return '';
    if (status === 'success' || status === 'approved') return '✓';
    if (status === 'published') return '';
    if (kind === 'generate') return '';
    if (kind === 'review') return '';
    if (kind === 'publish') return '';
    return '•';
  }

  function renderMonitorEvents(events) {
    const target = $('monitor-events');
    if (!target) return;
    events = (events || []).filter(function(ev) { return ev.kind !== 'publish'; });
    if (!events.length) {
      target.classList.add('empty');
      target.textContent = '暂无事件记录';
      return;
    }
    target.classList.remove('empty');
    target.innerHTML = events.map((ev) => {
      const tone = eventTone(ev.status);
      const icon = eventIcon(ev.kind, ev.status);
      const idTag = ev.story_id != null ? '<span class="me-id">#' + escapeHtml(ev.story_id) + '</span>' : '';
      const msg = ev.message || (ev.kind + ' / ' + ev.status);
      return '<div class="mon-event-row tone-' + tone + '" title="' + escapeHtml(ev.occurred_at || '') + '">' +
        '<span class="mon-event-icon">' + icon + '</span>' +
        '<span class="mon-event-kind">' + escapeHtml(ev.kind || '') + '/' + escapeHtml(ev.status || '') + '</span>' +
        '<span class="mon-event-msg">' + idTag + escapeHtml(msg) + '</span>' +
        '<span class="mon-event-time">' + escapeHtml(relTime(ev.occurred_at)) + '</span>' +
      '</div>';
    }).join('');
  }

  function renderMonitorHeatmap(errors) {
    const target = $('monitor-errors');
    if (!target) return;
    const now = new Date();
    // Build grid: rows = 7 days (most recent at top), cols = 24 hours
    const grid = [];
    for (let r = 0; r < 7; r++) {
      grid.push(new Array(24).fill(0));
    }
    let maxCount = 0;
    (errors || []).forEach((e) => {
      const d = parseTs(e.occurred_at);
      if (!d) return;
      const diffMs = now - d;
      const diffDays = Math.floor(diffMs / (86400 * 1000));
      if (diffDays < 0 || diffDays >= 7) return;
      const hour = d.getHours();
      grid[diffDays][hour] += 1;
      maxCount = Math.max(maxCount, grid[diffDays][hour]);
    });

    if (maxCount === 0) {
      target.classList.add('empty');
      target.textContent = '✓ 最近 7 天无错误事件';
      return;
    }
    target.classList.remove('empty');
    const heatLevel = (v) => {
      if (v === 0) return '';
      const p = v / maxCount;
      if (p > 0.75) return 'lv4';
      if (p > 0.5) return 'lv3';
      if (p > 0.25) return 'lv2';
      return 'lv1';
    };
    // Header row: hours 0..23
    const head = '<div class="mon-heat-row head"><span class="mon-heat-axis"></span>' +
      Array.from({ length: 24 }, (_, h) => '<span style="text-align:center">' + ((h % 4 === 0) ? h : '') + '</span>').join('') +
      '</div>';
    const rows = grid.map((row, r) => {
      const day = new Date(now); day.setDate(day.getDate() - r);
      const lbl = (r === 0 ? '今' : (r === 1 ? '昨' : String(day.getMonth() + 1) + '/' + day.getDate()));
      return '<div class="mon-heat-row"><span class="mon-heat-axis">' + lbl + '</span>' +
        row.map((v, h) => '<div class="mon-heat-cell ' + heatLevel(v) + '" title="' +
          lbl + ' ' + h + ':00 · 错误 ' + v + ' 次"></div>').join('') +
        '</div>';
    }).join('');
    target.innerHTML = head + rows;
  }

  function renderMonitorCompass(mon, conc) {
    const target = $('monitor-health');
    if (!target) return;
    const h = mon.health || {};
    const cells = [];
    cells.push({ icon: '', label: '数据库', value: h.db_path ? '就绪' : '未知', sub: h.db_path || '—', tone: h.db_path ? 'ok' : 'warn' });
    cells.push({ icon: '', label: '模型', value: mon.model || '未配置', sub: mon.dry_run ? 'Dry-run 模式' : 'Live 模式', tone: mon.model ? 'ok' : 'warn' });
    const sch = mon.schedule || {};
    cells.push({ icon: '', label: '调度器', value: (sch.enabled ? '启用' : '手动模式'), sub: sch.cron || sch.interval || '—', tone: sch.enabled ? 'ok' : 'warn' });
    const ln = mon.long_novel || {};
    cells.push({ icon: '', label: '长篇小说', value: (ln.books_total || 0) + ' 本', sub: (ln.chapters_done || 0) + '/' + (ln.chapters_total || 0) + ' 章 · ' + fmtNum(ln.words_total || 0) + '字', tone: (ln.books_total || 0) > 0 ? 'ok' : 'warn' });
    const cMax = (conc && conc.max_concurrent) || 0;
    const cUse = (conc && conc.in_use) || 0;
    cells.push({ icon: '', label: '并发槽', value: cMax > 0 ? (cUse + ' / ' + cMax) : '—', sub: cMax > 0 ? (cUse >= cMax ? '已满载' : '有空闲槽') : '未读取', tone: cMax > 0 ? (cUse >= cMax ? 'warn' : 'ok') : 'warn' });
    target.classList.remove('empty');
    target.innerHTML = cells.map((c) => (
      '<div class="compass-cell tone-' + c.tone + '">' +
        '<div class="compass-icon">' + c.icon + '</div>' +
        '<div class="compass-info">' +
          '<div class="compass-label">' + escapeHtml(c.label) + '</div>' +
          '<div class="compass-value" title="' + escapeHtml(c.value) + '">' + escapeHtml(c.value) + '</div>' +
          '<div class="compass-sub" title="' + escapeHtml(c.sub) + '">' + escapeHtml(c.sub) + '</div>' +
        '</div>' +
      '</div>'
    )).join('');
  }

  // ============================================================
  //   Overview bindings: refresh button + timeline click → detail
  // ============================================================

  function bindOverviewExtras() {
    const refreshBtn = $('btn-overview-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', () => loadOverview());
    const jumpBtn = $('btn-overview-jump-stories');
    if (jumpBtn) jumpBtn.addEventListener('click', () => showSection('generate'));
    const timeline = $('overview-recent');
    if (timeline) {
      timeline.addEventListener('click', (ev) => {
        const lnRow = ev.target.closest && ev.target.closest('[data-ln-book]');
        if (lnRow) {
          showSection('long-novel');
          return;
        }
        const row = ev.target.closest && ev.target.closest('[data-detail]');
        if (!row) return;
        const id = row.getAttribute('data-detail');
        if (id && typeof openInboxDetail === 'function') {
          openInboxDetail(Number(id));
        }
      });
    }
  }

  function renderStoryTable(stories, opts = {}) {
    const showActions = opts.showActions !== false;
    const head =
      '<table><thead><tr>' +
      '<th>ID</th><th>标题</th><th>状态</th><th>分数</th><th>更新时间</th>' +
      (showActions ? '<th>操作</th>' : '') +
      '</tr></thead><tbody>';
    const rows = stories.map((story) => {
      const score = story.score != null ? story.score : '—';
      const updated = story.updated_at || story.created_at || '';
      const status = escapeHtml(story.status || '');
      const dryRunMarker = story.is_dry_run ? '<span class="dryrun-marker" title="演练模式生成的作品">演练</span>' : '';
      const rowCls = story.is_dry_run ? ' class="story-dryrun"' : '';
      const actions = showActions
        ? '<td><div class="actions">' +
          '<button class="tiny ghost" data-detail="' + story.id + '">详情</button>' +
          '<button class="tiny success" data-approve="' + story.id + '">批准</button>' +
          '<button class="tiny danger" data-reject="' + story.id + '">拒绝</button>' +
          '<button class="tiny" data-ai="' + story.id + '">AI 审核</button>' +
          '<button class="tiny ghost" data-delete="' + story.id + '">删除</button>' +
          '</div></td>'
        : '';
      return '<tr' + rowCls + '>' +
        '<td>#' + escapeHtml(story.id) + '</td>' +
        '<td>' + dryRunMarker + escapeHtml(story.title || '') + '</td>' +
        '<td><span class="badge ' + status + '">' + status + '</span></td>' +
        '<td>' + escapeHtml(score) + '</td>' +
        '<td>' + escapeHtml(updated) + '</td>' +
        actions +
        '</tr>';
    }).join('');
    return head + rows + '</tbody></table>';
  }

  // ---------- Execution Console ----------
  let _consoleTimer = null;

  function bindConsole() {
    const runBtn = $('btn-console-run');
    const cancelBtn = $('btn-console-cancel');
    if (runBtn) {
      runBtn.addEventListener('click', async () => {
        const release = withBusy(runBtn, '启动中…');
        try {
          const data = await api('/api/console/run-now', { method: 'POST', body: {} });
          toast('已启动原子任务 #' + data.story_id, 'success');
          if (data.story_id != null) ensureProgressTracking(Number(data.story_id));
          loadConsoleStatus();
          loadInbox();
          setTimeout(loadInbox, 1500);
        } catch (err) {
          toast('启动失败：' + err.message, 'error');
        } finally { release(); }
      });
    }
    if (cancelBtn) {
      cancelBtn.addEventListener('click', async () => {
        if (cancelBtn.disabled) return;
        const release = withBusy(cancelBtn, '取消中…');
        try {
          const data = await api('/api/console/cancel', { method: 'POST', body: {} });
          toast(data.message || '已请求取消', 'info');
          loadConsoleStatus();
        } catch (err) {
          toast('取消失败：' + err.message, 'error');
        } finally { release(); }
      });
    }
  }

  async function loadConsoleStatus() {
    try {
      const data = await api('/api/console/status');
      const cur = data.current_task;
      const curEl = $('console-current');
      const cancelBtn = $('btn-console-cancel');
      const runBtn = $('btn-console-run');
      if (cur) {
        const phase = cur.current_phase || cur.phase || '';
        if (curEl) curEl.textContent = '#' + (cur.story_id || '?') + ' ' + phase;
        if (cancelBtn) cancelBtn.disabled = false;
        if (runBtn) runBtn.disabled = true;
        if (cur.story_id != null) ensureProgressTracking(Number(cur.story_id));
      } else {
        if (curEl) curEl.textContent = '空闲';
        if (cancelBtn) cancelBtn.disabled = true;
        if (runBtn) runBtn.disabled = false;
      }
    } catch (err) {
      // silent: console polling is best-effort
    }
  }

  function startConsoleTimer() {
    if (_consoleTimer) return;
    loadConsoleStatus();
    _consoleTimer = setInterval(loadConsoleStatus, 5000);
  }

  // ---------- Generation progress (Phase strip + timeline + artifact preview) ----------
  let _progressTimer = null;
  let _progressStoryId = null;
  let _progressLastState = null;
  let _progressIdleTicks = 0;
  const _PHASE_LABELS = {
    phase_0: 'phase_0 选题',
    phase_1: 'phase_1 框架/简介',
    phase_2: 'phase_2 大纲',
    phase_3: 'phase_3 逐节',
    phase_4: 'phase_4 精修',
    phase_5: 'phase_5 去 AI 味',
  };
  const _PHASE_ORDER = ['phase_0','phase_1','phase_2','phase_3','phase_4','phase_5'];

  function ensureProgressTracking(storyId) {
    if (storyId == null) return;
    if (_progressStoryId !== storyId) {
      _progressStoryId = storyId;
      _progressIdleTicks = 0;
      _progressLastState = null;
      $('progress-panel').style.display = 'block';
      loadProgress();
    }
    if (_progressTimer == null) {
      _progressTimer = setInterval(loadProgress, 2000);
    }
  }

  function noteProgressIdle() {
    if (_progressStoryId == null) return;
    _progressIdleTicks += 1;
    if (_progressIdleTicks >= 30) {
      stopProgressTracking({ keepPanel: true });
    }
  }

  function stopProgressTracking({ keepPanel } = {}) {
    if (_progressTimer != null) {
      clearInterval(_progressTimer);
      _progressTimer = null;
    }
    if (!keepPanel) {
      $('progress-panel').style.display = 'none';
      _progressStoryId = null;
    }
  }

  async function loadProgress() {
    if (_progressStoryId == null) return;
    let data;
    try {
      data = await api('/api/stories/' + _progressStoryId + '/phases');
    } catch (err) {
      // story may have been deleted; stop polling rather than spam errors
      stopProgressTracking({ keepPanel: false });
      return;
    }
    renderRetryBanner(data);
    renderPhaseStrip(data);
    renderSectionSub(data);
    renderTimeline(data);
    const story = $('progress-story');
    if (story) {
      const stateLabel = data.label ? ' · ' + data.label : '';
      story.textContent = '#' + data.story_id + stateLabel;
    }
    if (data.state === 'done' || data.state === 'failed') {
      // Pipeline reached a terminal state — keep the panel visible but stop polling.
      stopProgressTracking({ keepPanel: true });
    } else if (_progressLastState && _progressLastState === data.current_phase) {
      noteProgressIdle();
    } else {
      _progressIdleTicks = 0;
    }
    _progressLastState = data.current_phase;
  }

  function renderRetryBanner(data) {
    const banner = $('progress-retry-banner');
    if (!banner) return;
    const retry = data.retry;
    if (!retry || !retry.attempt || retry.attempt < 2) {
      banner.style.display = 'none';
      banner.innerHTML = '';
      return;
    }
    const prevPhase = retry.previous_failed_at;
    const prevLabel = prevPhase ? (_PHASE_LABELS[prevPhase] || prevPhase) : null;
    const tail = prevLabel ? '，上一轮在 <strong>' + escapeHtml(prevLabel) + '</strong> 失败' : '';
    banner.innerHTML = '这是 <strong>第 ' + retry.attempt + ' 次尝试</strong>' + tail
      + '。pipeline 自动从 phase_0 重跑，每段产物会被覆盖。';
    banner.style.display = 'block';
  }

  function renderPhaseStrip(data) {
    const strip = $('progress-strip');
    if (!strip) return;
    // Use preset_steps if available (custom preset), otherwise fall back to hardcoded steps
    const presetSteps = Array.isArray(data.preset_steps) && data.preset_steps.length > 0 ? data.preset_steps : null;
    const steps = presetSteps || (data.steps || []);
    const artifacts = data.artifacts || {};
    // Prefer latest attempt's per-phase durations so retries don't pollute the chips.
    const attempts = data.attempts || [];
    const lastAttempt = attempts.length ? attempts[attempts.length - 1] : null;
    const lastByPhase = {};
    if (lastAttempt) {
      (lastAttempt.phases || []).forEach((p) => { lastByPhase[p.phase] = p; });
    }
    // Fall back to flat timeline (single attempt case).
    (data.timeline || []).forEach((t) => {
      if (!lastByPhase[t.phase]) lastByPhase[t.phase] = t;
    });
    strip.innerHTML = steps.map((step) => {
      const arts = artifacts[step.phase] || [];
      const ready = arts.some((a) => a.exists);
      const clickable = (step.status === 'done' || step.status === 'failed') && ready;
      const tl = lastByPhase[step.phase];
      let meta = '';
      if (tl && tl.duration_seconds != null) {
        meta = formatDur(tl.duration_seconds);
      } else if (step.status === 'in_progress') {
        meta = '进行中…';
      } else if (step.status === 'pending') {
        meta = '待开始';
      } else if (step.status === 'rewrite') {
        meta = 'R2 重写';
      } else if (step.status === 'failed') {
        meta = '失败';
      }
      return '<div class="phase-chip" data-status="' + step.status + '" data-phase="' + step.phase + '" data-clickable="' + (clickable ? '1' : '0') + '">'
        + '<span class="phase-chip-label">' + escapeHtml(step.label) + '</span>'
        + '<span class="phase-chip-meta">' + escapeHtml(meta) + '</span>'
        + '</div>';
    }).join('');
    strip.querySelectorAll('.phase-chip[data-clickable="1"]').forEach((el) => {
      el.addEventListener('click', () => {
        const phase = el.dataset.phase;
        const arts = (artifacts[phase] || []).filter((a) => a.exists);
        if (!arts.length) return;
        openArtifact(data.story_id, arts[0].name);
      });
    });
  }

  function renderSectionSub(data) {
    const target = $('progress-section-sub');
    if (!target) return;
    const sec = data.phase_3_section;
    if (!sec) {
      target.innerHTML = '';
      return;
    }
    const total = sec.total;
    const cur = sec.current || 0;
    let bar = '';
    if (total) {
      const pct = Math.max(0, Math.min(100, Math.round(cur / total * 100)));
      bar = '<span class="phase-section-bar"><span style="width:' + pct + '%"></span></span>';
    }
    const totalTxt = total ? ('/' + total + ' 节') : ' 节（总数未知）';
    target.innerHTML = '🪄 phase_3 子进度：第 <strong>' + cur + '</strong>' + totalTxt + bar;
  }

  function renderTimeline(data) {
    const ol = $('progress-timeline');
    if (!ol) return;
    const attempts = data.attempts || [];
    const flat = data.timeline || [];
    if (attempts.length === 0 && flat.length === 0) {
      ol.innerHTML = '<li class="phase-timeline-empty">尚无 phase 进入记录。</li>';
      return;
    }
    if (attempts.length <= 1) {
      // Single attempt — render the flat list (matches pre-retry behaviour).
      const phases = attempts.length === 1 ? attempts[0].phases : flat;
      ol.innerHTML = phases.map(renderTimelineLine).join('') || '<li class="phase-timeline-empty">尚无 phase 进入记录。</li>';
      return;
    }
    const html = attempts.map((a) => {
      const stateTag = attemptStateTag(a.status);
      const failedTail = a.failed_at ? '（失败于 ' + escapeHtml(_PHASE_LABELS[a.failed_at] || a.failed_at) + '）' : '';
      const startTime = a.started_at ? formatLocalTimeShort(a.started_at) : '';
      const head = '<li class="pt-attempt-head" data-status="' + a.status + '">'
        + stateTag + ' 尝试 #' + a.attempt + ' · ' + escapeHtml(startTime)
        + (failedTail ? ' ' + failedTail : '')
        + '</li>';
      const phases = (a.phases || []).map(renderTimelineLine).join('');
      return head + phases;
    }).join('');
    ol.innerHTML = html;
  }

  function renderTimelineLine(entry) {
    const ts = formatLocalTimeShort(entry.entered_at);
    const dur = entry.duration_seconds != null ? formatDur(entry.duration_seconds) : '进行中';
    const stateTag = entry.status === 'done'
      ? '✓'
      : (entry.status === 'failed' ? '⚠' : (entry.status === 'rewrite' ? '↻' : '…'));
    return '<li data-status="' + entry.status + '">'
      + '<span class="pt-time">' + escapeHtml(ts) + '</span>'
      + '<span class="pt-label">' + stateTag + ' ' + escapeHtml(entry.label) + '</span>'
      + '<span class="pt-dur">' + escapeHtml(dur) + '</span>'
      + '</li>';
  }

  function attemptStateTag(status) {
    if (status === 'failed') return '⚠';
    if (status === 'done') return '✓';
    if (status === 'in_progress') return '';
    if (status === 'rewrite') return '↻';
    return '·';
  }

  function formatDur(secs) {
    const s = Math.max(0, Math.round(Number(secs) || 0));
    if (s < 60) return s + 's';
    const m = Math.floor(s / 60);
    const r = s % 60;
    return m + 'm ' + r + 's';
  }

  function formatLocalTimeShort(iso) {
    if (!iso) return '—';
    let s = String(iso).trim();
    if (s.endsWith('Z')) s = s.slice(0, -1);
    if (s.indexOf('T') === -1 && s.indexOf(' ') !== -1) s = s.replace(' ', 'T');
    if (s.indexOf('+') === -1 && s.indexOf('Z') === -1) s = s + 'Z';
    const d = new Date(s);
    if (isNaN(d.getTime())) return iso;
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    return hh + ':' + mm + ':' + ss;
  }

  async function openArtifact(storyId, filename) {
    const modal = $('artifact-modal');
    const titleEl = $('artifact-modal-title');
    const bodyEl = $('artifact-modal-body');
    if (!modal || !titleEl || !bodyEl) return;
    titleEl.textContent = '#' + storyId + ' / ' + filename;
    bodyEl.textContent = '加载中…';
    modal.style.display = 'flex';
    try {
      const url = '/api/stories/' + storyId + '/files/' + encodeURIComponent(filename);
      const data = await api(url);
      bodyEl.textContent = data.content || '(空文件)';
    } catch (err) {
      bodyEl.textContent = '加载失败：' + err.message;
    }
  }

  function bindArtifactModal() {
    const modal = $('artifact-modal');
    const closeBtn = $('artifact-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', () => { modal.style.display = 'none'; });
    if (modal) {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.style.display = 'none';
      });
    }
  }

  // ---------- Review ----------
  async function loadReviewList() {
    const status = $('review-status').value;
    const target = $('review-list');
    target.classList.add('empty');
    target.textContent = '加载中…';
    try {
      const query = status ? '?status=' + encodeURIComponent(status) + '&limit=50' : '?limit=50';
      const data = await api('/api/stories' + query);
      const stories = Array.isArray(data.stories) ? data.stories : [];
      if (stories.length === 0) {
        target.classList.add('empty');
        target.textContent = '当前没有匹配状态的作品。';
        return;
      }
      target.classList.remove('empty');
      target.innerHTML = renderStoryTable(stories, { showActions: true });
      bindReviewRowActions(target);
    } catch (err) {
      target.textContent = '加载失败：' + err.message;
      toast(err.message, 'error');
    }
  }

  function bindReviewRowActions(root) {
    root.addEventListener('click', async (event) => {
      const target = event.target.closest('button');
      if (!target) return;
      if (target.dataset.detail) return openStoryModal(Number(target.dataset.detail));
      if (target.dataset.approve) return reviewAction(target, 'approve', target.dataset.approve);
      if (target.dataset.reject) return reviewAction(target, 'reject', target.dataset.reject);
      if (target.dataset.ai) return reviewAction(target, 'ai', target.dataset.ai);
      if (target.dataset.delete) return deleteStory(target, target.dataset.delete);
    }, { once: true });
  }

  async function reviewAction(button, action, storyId) {
    const release = withBusy(button, '处理中…');
    try {
      let url = '/api/review/' + storyId + '/' + action;
      let body = undefined;
      if (action === 'reject') {
        const note = window.prompt('拒绝原因（可空）', '人工拒绝。');
        if (note === null) { release(); loadReviewList(); return; }
        body = { review_notes: note || '人工拒绝。' };
      }
      const data = await api(url, { method: 'POST', body });
      toast(data.message || '已完成', data.ok ? 'success' : 'warn');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      release();
      loadReviewList();
      loadOverview();
    }
  }

  async function deleteStory(button, storyId) {
    showConfirm('确认删除作品 #' + storyId + '？此操作不可撤销。', async function() {
      const release = withBusy(button, '删除中…');
      try {
        const data = await api('/api/stories/' + storyId, { method: 'DELETE' });
        toast(data.message || '已删除', 'success');
      } catch (err) {
        toast(err.message, 'error');
      } finally {
        release();
        loadInbox();
        loadOverview();
      }
    });
  }

  async function openStoryModal(storyId) {
    const modal = $('story-modal');
    const body = $('story-modal-body');
    modal.classList.add('show');
    body.classList.add('empty');
    body.textContent = '加载中…';
    try {
      const data = await api('/api/stories/' + storyId);
      const story = data.story || {};
      $('story-modal-title').textContent = '#' + story.id + '  ' + (story.title || '');
      const detail = story.review_detail || {};
      const issues = Array.isArray(detail.issues) ? detail.issues : [];
      const suggestions = Array.isArray(detail.suggestions) ? detail.suggestions : [];
      const dimensionScores = detail.dimension_scores || {};
      const dimensionItems = Object.keys(dimensionScores).map((key) => '<li>' + escapeHtml(key) + '：' + escapeHtml(dimensionScores[key]) + '</li>').join('');
      body.classList.remove('empty');
      body.innerHTML =
        '<form id="story-edit" class="grid">' +
        '<label class="full">标题<input name="title" required value="' + escapeHtml(story.title || '') + '"></label>' +
        '<label class="full">内容<textarea name="content" required>' + escapeHtml(story.content || '') + '</textarea></label>' +
        '<label class="full">备注<input name="review_notes" value="' + escapeHtml(story.review_notes || '') + '"></label>' +
        '<div class="full meta">状态：<span class="badge ' + escapeHtml(story.status || '') + '">' + escapeHtml(story.status || '') + '</span>　分数：' + escapeHtml(story.score != null ? story.score : '—') + '　重写次数：' + escapeHtml(story.retry_count) + '</div>' +
        (issues.length ? '<div class="full"><strong>问题：</strong><ul>' + issues.map((i) => '<li>' + escapeHtml(i) + '</li>').join('') + '</ul></div>' : '') +
        (suggestions.length ? '<div class="full"><strong>建议：</strong><ul>' + suggestions.map((s) => '<li>' + escapeHtml(s) + '</li>').join('') + '</ul></div>' : '') +
        (dimensionItems ? '<div class="full"><strong>维度分：</strong><ul>' + dimensionItems + '</ul></div>' : '') +
        '<div class="full modal-actions">' +
        '<button type="submit" class="primary">保存编辑</button>' +
        '<button type="button" class="success" data-approve="' + story.id + '">批准</button>' +
        '<button type="button" class="warning" data-ai="' + story.id + '">AI 审核</button>' +
        '<button type="button" class="danger" data-reject="' + story.id + '">拒绝</button>' +
        '<button type="button" class="ghost" data-delete="' + story.id + '">删除</button>' +
        '</div>' +
        '</form>';

      $('story-edit').addEventListener('submit', async (event) => {
        event.preventDefault();
        const release = withBusy(event.submitter, '保存中…');
        const formData = new FormData(event.target);
        try {
          const result = await api('/api/review/' + story.id + '/save', {
            method: 'POST',
            body: {
              title: formData.get('title'),
              content: formData.get('content'),
              review_notes: formData.get('review_notes'),
            },
          });
          toast(result.message || '已保存', 'success');
        } catch (err) {
          toast(err.message, 'error');
        } finally {
          release();
        }
      });

      body.querySelectorAll('button[data-approve]').forEach((btn) => btn.addEventListener('click', () => reviewAction(btn, 'approve', btn.dataset.approve)));
      body.querySelectorAll('button[data-reject]').forEach((btn) => btn.addEventListener('click', () => reviewAction(btn, 'reject', btn.dataset.reject)));
      body.querySelectorAll('button[data-ai]').forEach((btn) => btn.addEventListener('click', () => reviewAction(btn, 'ai', btn.dataset.ai)));
      body.querySelectorAll('button[data-delete]').forEach((btn) => btn.addEventListener('click', () => deleteStory(btn, btn.dataset.delete)));
    } catch (err) {
      body.textContent = '加载失败：' + err.message;
      toast(err.message, 'error');
    }
  }

  // ---------- Review (审核队列页已下线；仅保留模态用于后续收件箱阶段重用) ----------
  async function loadReviewList() { /* removed: 审核队列页已下线 */ }

  function bindReview() {
    // story-modal 关闭事件仍然挂上，便于阶段 4 收件箱复用
    const modal = $('story-modal');
    if (!modal) return;
    $('story-modal-close').addEventListener('click', () => modal.classList.remove('show'));
    modal.addEventListener('click', (event) => {
      if (event.target === modal) modal.classList.remove('show');
    });
  }

  // ---------- Inbox ----------

  const INBOX_STATUS_LABELS = {
    pending: '待生成',
    needs_human: '需人工审核',
    approved: '已批准',
    published: '已完成',
    publish_paused: '已暂停',
    publish_failed: '失败',
    failed: '失败',
    rejected: '已拒绝',
    cancelled: '已取消',
    paused_login_required: '需处理',
    paused_zhuque_anomaly: '朱雀检测异常',
    rejected_ai: '朱雀检测拒绝',
  };

  function inboxStatusBadge(status) {
    const cls = (status || '').replace(/[^a-z_]/gi, '_');
    const label = INBOX_STATUS_LABELS[status] || status || '—';
    return '<span class="inbox-badge ' + cls + '">' + escapeHtml(label) + '</span>';
  }

  function inboxRowActions(story) {
    const status = story.status || '';
    const sid = story.id;
    let html = '';
    // 详情：查看每步骤结果
    html += '<button class="tiny ghost" data-detail="' + sid + '">步骤</button>';
    // 删除：所有行都可以删
    html += '<button class="tiny danger" data-delete="' + sid + '">删除</button>';
    return html;
  }

  function formatInboxTime(value) {
    if (!value) return '—';
    return String(value).replace('T', ' ').slice(0, 16);
  }

  function bindInbox() {
    const refreshBtn = $('btn-inbox-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', loadInbox);

    const list = $('inbox-list');
    if (list) {
      list.addEventListener('click', (event) => {
        const delBtn = event.target.closest('button[data-delete]');
        if (delBtn) {
          event.stopPropagation();
          deleteStory(delBtn, Number(delBtn.dataset.delete));
          return;
        }
        const detailBtn = event.target.closest('button[data-detail]');
        if (detailBtn) {
          event.stopPropagation();
          openInboxDetail(Number(detailBtn.dataset.detail));
          return;
        }
        const row = event.target.closest('.inbox-row');
        if (row && row.dataset.row) openInboxDetail(Number(row.dataset.row));
      });
    }

    const modal = $('inbox-detail-modal');
    const close = $('inbox-detail-close');
    if (close && modal) close.addEventListener('click', () => modal.classList.remove('show'));
    if (modal) {
      modal.addEventListener('click', (event) => {
        if (event.target === modal) modal.classList.remove('show');
      });
    }
  }

  async function loadInbox() {
    const target = $('inbox-list');
    if (!target) return;
    target.classList.add('empty');
    target.textContent = '加载中…';
    try {
      const data = await api('/api/stories?limit=20');
      const stories = Array.isArray(data.stories) ? data.stories : [];
      const summary = $('inbox-summary');
      if (summary) {
        const counts = {};
        stories.forEach((s) => { counts[s.status] = (counts[s.status] || 0) + 1; });
        const pending = (counts.needs_human || 0) + (counts.approved || 0) + (counts.publish_paused || 0);
        summary.textContent = '共 ' + stories.length + ' 篇' + (pending ? '，待处理 ' + pending + ' 篇' : '');
      }
      if (stories.length === 0) {
        target.classList.add('empty');
        target.innerHTML = '<div class="inbox-empty">还没有作品。点上面"立即执行一次"开始生成第一篇。</div>';
        return;
      }
      target.classList.remove('empty');
      target.innerHTML = stories.map((story) => {
        const score = story.ai_review_score != null ? Number(story.ai_review_score).toFixed(0) : '—';
        return '<div class="inbox-row" data-row="' + story.id + '">' +
          '<span class="inbox-id">#' + escapeHtml(story.id) + '</span>' +
          '<span class="inbox-title" title="' + escapeHtml(story.title || '') + '">' + escapeHtml(story.title || '(未生成)') + '</span>' +
          inboxStatusBadge(story.status) +
          '<span class="inbox-meta">分:' + escapeHtml(score) + ' · ' + escapeHtml(formatInboxTime(story.updated_at || story.created_at)) + '</span>' +
          '<span class="inbox-actions">' + inboxRowActions(story) + '</span>' +
          '</div>';
      }).join('');
    } catch (err) {
      target.textContent = '加载失败：' + err.message;
      toast('收件箱加载失败：' + err.message, 'error');
    }
  }

  async function openInboxDetail(storyId) {
    const modal = $('inbox-detail-modal');
    const titleEl = $('inbox-detail-title');
    const metaEl = $('inbox-detail-meta');
    const contentEl = $('inbox-detail-content');
    const footEl = $('inbox-detail-foot');
    modal.classList.add('show');
    titleEl.textContent = '#' + storyId + ' 加载中…';
    metaEl.innerHTML = '';
    contentEl.textContent = '加载中…';
    footEl.innerHTML = '<button class="btn-ghost" id="inbox-detail-cancel">关闭</button>';
    $('inbox-detail-cancel').addEventListener('click', () => modal.classList.remove('show'));
    try {
      // 并行拉取 story 详情 + phases 数据
      const [sResp, pResp] = await Promise.all([
        api('/api/stories/' + storyId),
        api('/api/stories/' + storyId + '/phases'),
      ]);
      const story = sResp.story || {};
      const phases = pResp.ok ? pResp : null;
      titleEl.textContent = '#' + story.id + '  ' + (story.title || '(无标题)');
      const score = story.ai_review_score != null ? Number(story.ai_review_score).toFixed(1) : '—';
      const cost = story.pipeline_cost_cny != null ? '¥' + Number(story.pipeline_cost_cny).toFixed(4) : '—';
      metaEl.innerHTML =
        '<span>状态：' + inboxStatusBadge(story.status) + '</span>' +
        '<span>当前阶段：' + escapeHtml(story.current_phase || '—') + '</span>' +
        '<span>评分：' + escapeHtml(score) + '</span>' +
        '<span>累计成本：' + escapeHtml(cost) + '</span>' +
        '<span>更新：' + escapeHtml(formatInboxTime(story.updated_at || story.created_at)) + '</span>';

      // 步骤条（可点击切换）+ 产物文件
      let phaseHtml = '';
      var displaySteps = (Array.isArray(phases.preset_steps) && phases.preset_steps.length > 0)
        ? phases.preset_steps : (Array.isArray(phases.steps) ? phases.steps : []);
      if (displaySteps.length > 0) {
        phaseHtml += '<div class="phase-strip" style="margin-top:0.5rem">';
        displaySteps.forEach(function(step, idx) {
          const icon = step.status === 'done' ? '✓' : step.status === 'running' ? '' : step.status === 'failed' ? '✕' : step.status === 'skipped' ? '' : '';
          phaseHtml += '<div class="phase-chip" data-phase="' + escapeHtml(step.phase) + '" data-idx="' + idx + '" data-status="' + escapeHtml(step.status) + '" title="点击查看' + escapeHtml(step.label) + '" style="cursor:pointer">' +
            '<span class="phase-chip-icon">' + icon + '</span>' +
            '<span class="phase-chip-label">' + escapeHtml(step.label) + '</span>' +
            '</div>';
        });
        if (phases.percent != null) {
          phaseHtml += '<span class="inbox-meta" style="margin-left:0.5rem">' + phases.percent + '%</span>';
        }
        phaseHtml += '</div>';
        // Per-phase 预览区
        phaseHtml += '<div id="phase-preview" style="display:none;margin-top:0.5rem;padding:0.5rem;background:var(--panel-soft);border-radius:4px;max-height:500px;overflow:auto">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.3rem">' +
          '<strong id="phase-preview-title"></strong>' +
          '<div style="display:flex;gap:0.3rem">' +
          '<button class="btn-primary tiny" id="phase-preview-prompt-btn" style="display:none">提示词</button>' +
          '<button class="btn-warning tiny" id="phase-preview-rerun" style="display:none">重跑此步骤</button>' +
          '<button class="btn-ghost tiny" id="phase-preview-close">✕</button>' +
          '</div>' +
          '</div>' +
          '<pre id="phase-preview-content" style="white-space:pre-wrap;font-size:0.8rem;margin:0"></pre>' +
          '</div>';
      }
      // 产物文件链接（artifacts 是 {phase: [{name, exists, size_bytes}]} 字典）
      const artifactsMap = (phases && phases.artifacts && typeof phases.artifacts === 'object' && !Array.isArray(phases.artifacts)) ? phases.artifacts : {};
      const allArtifacts = Object.values(artifactsMap).flat();
      if (allArtifacts.length > 0) {
        phaseHtml += '<div class="inbox-meta" style="margin-top:0.4rem; display:flex; gap:0.5rem; flex-wrap:wrap">';
        phaseHtml += '<strong>产物：</strong>';
        allArtifacts.forEach(function(a) {
          if (!a.exists) return;
          const label = a.name || '';
          const href = '/api/stories/' + storyId + '/files/' + encodeURIComponent(label);
          phaseHtml += '<a href="' + href + '" target="_blank" class="inbox-badge" style="text-decoration:none;cursor:pointer" title="' + escapeHtml(String(a.size_bytes || '') + ' bytes') + '">' + escapeHtml(label) + '</a>';
        });
        phaseHtml += '</div>';
      }
      // timeline 折叠
      if (phases && Array.isArray(phases.timeline) && phases.timeline.length > 0) {
        phaseHtml += '<details style="margin-top:0.4rem; font-size:0.8rem"><summary>阶段时间线（' + phases.timeline.length + ' 条）</summary>';
        phaseHtml += '<table style="width:100%; border-collapse:collapse">';
        phases.timeline.forEach(function(t) {
          phaseHtml += '<tr><td style="padding:2px 6px">' + escapeHtml(t.phase || '') + '</td><td style="color:var(--muted)">' + escapeHtml(t.entered_at || '') + '</td><td>' + escapeHtml(t.duration || '') + '</td></tr>';
        });
        phaseHtml += '</table></details>';
      }

      // summary 内容
      let body = '';
      if (story.summary) body = story.summary;
      if (!body) body = '(暂无可预览内容)';
      contentEl.innerHTML = phaseHtml + '<pre style="white-space:pre-wrap;margin-top:0.5rem;font-size:0.85rem">' + escapeHtml(body) + '</pre>';

      // footer
      footEl.innerHTML = '<button class="btn-ghost" id="inbox-detail-cancel">关闭</button>' +
        '<button class="btn-danger" id="inbox-detail-delete">删除</button>';
      $('inbox-detail-cancel').addEventListener('click', () => modal.classList.remove('show'));
      $('inbox-detail-delete').addEventListener('click', () => {
        showConfirm('确定删除 #' + storyId + '？此操作不可撤销。', function() {
          modal.classList.remove('show');
          deleteStoryFromDetail(storyId);
        });
      });
      // 步骤 chip 点击 → 预览该 phase 产物
      contentEl.querySelectorAll('.phase-chip[data-phase]').forEach(function(chip) {
        chip.addEventListener('click', async function() {
          const phase = chip.dataset.phase;
          const status = chip.dataset.status;
          const label = (chip.querySelector('.phase-chip-label') || {}).textContent || phase;
          // artifactsMap 是 {phase: [{name, exists, size_bytes}]} 字典
          const phaseFiles = artifactsMap[phase] || [];
          const existingFile = phaseFiles.find(function(a) { return a.exists && a.name; });
          const knownName = existingFile ? existingFile.name : (phaseFiles.length > 0 ? phaseFiles[0].name : null);
          const previewEl = $('phase-preview');
          const titleEl = $('phase-preview-title');
          const contentEl2 = $('phase-preview-content');
          if (!previewEl || !titleEl || !contentEl2) return;
          previewEl.style.display = 'block';
          // highlight 当前 chip
          contentEl.querySelectorAll('.phase-chip').forEach(function(c) { c.style.outline = ''; });
          chip.style.outline = '2px solid var(--primary)';
          // show rerun button
          var rerunBtn = $('phase-preview-rerun');
          if (rerunBtn) rerunBtn.style.display = '';
          var promptBtn = $('phase-preview-prompt-btn');
          if (promptBtn) promptBtn.style.display = '';

          if (!knownName) {
            titleEl.textContent = escapeHtml(label) + '（' + escapeHtml(status) + '）';
            contentEl2.textContent = '该步骤暂无产物文件（' + (status === 'pending' ? '尚未执行' : status === 'running' ? '正在执行中' : '未生成产物') + '）。';
            return;
          }
          // 有产物文件 → 加载
          const filename = knownName;
          titleEl.textContent = '' + escapeHtml(filename) + (existingFile && existingFile.size_bytes != null ? '  (' + existingFile.size_bytes + ' bytes)' : '');
          contentEl2.textContent = '加载中…';
          try {
            const fileData = await api('/api/stories/' + storyId + '/files/' + encodeURIComponent(filename));
            contentEl2.textContent = (fileData.content || '').substring(0, 50000);
          } catch (err2) {
            contentEl2.textContent = '加载失败：' + err2.message;
          }
        });
      });
      const previewClose = $('phase-preview-close');
      if (previewClose) previewClose.addEventListener('click', function() {
        $('phase-preview').style.display = 'none';
        contentEl.querySelectorAll('.phase-chip').forEach(function(c) { c.style.outline = ''; });
      });
      // 重跑此步骤按钮 — 两步确认：仅当前步骤 / 及之后所有
      const rerunBtn = $('phase-preview-rerun');
      if (rerunBtn) rerunBtn.addEventListener('click', async function() {
        const activeChip = contentEl.querySelector('.phase-chip[style*="outline"]');
        if (!activeChip) return;
        const phase = activeChip.dataset.phase;
        const label = (activeChip.querySelector('.phase-chip-label') || {}).textContent || phase;
        const singleOnly = confirm('只重跑「' + label + '」这一个步骤？\n\n按"确定"=仅重跑当前步骤\n按"取消"=选择重跑此步骤及之后所有步骤');
        const mode = singleOnly ? 'single' : 'all';
        const release = withBusy(rerunBtn, '重跑中…');
        try {
          const data = await api('/api/stories/' + storyId + '/rerun-phase/' + encodeURIComponent(phase) + '?mode=' + mode, { method: 'POST' });
          toast(data.message || '重跑已启动', 'success');
          $('phase-preview').style.display = 'none';
          modal.classList.remove('show');
          loadInbox();
        } catch (err2) {
          toast('重跑失败：' + err2.message, 'error');
        } finally { release(); }
      });
      // 提示词 → 独立弹窗编辑器
      const promptBtn = $('phase-preview-prompt-btn');
      if (promptBtn) promptBtn.addEventListener('click', async function() {
        const activeChip = contentEl.querySelector('.phase-chip[style*="outline"]');
        if (!activeChip) return;
        const phase = activeChip.dataset.phase;
        const label = (activeChip.querySelector('.phase-chip-label') || {}).textContent || phase;
        openPromptWindow(phase, label);
      });
    } catch (err) {
      contentEl.textContent = '加载失败：' + err.message;
      toast(err.message, 'error');
    }
  }

  var _promptWindows = {};

  function openPromptWindow(phase, label) {
    if (_promptWindows[phase] && !_promptWindows[phase].closed) {
      _promptWindows[phase].focus();
      return;
    }
    var baseUrl = window.location.origin;
    var w = window.open('about:blank', 'prompt-' + phase, 'width=900,height=700,resizable,scrollbars');
    if (!w) { toast('弹窗被拦截，请允许弹窗后重试', 'warn'); return; }
    _promptWindows[phase] = w;

    // 先用 document.write 输出骨架，避免 blob URL 与 load 事件竞态
    w.document.write(
      '<!DOCTYPE html><html><head><meta charset="utf-8"><title>' + escapeHtml(label) + '</title><style>'
      + 'body{margin:0;font-family:system-ui,sans-serif;background:#1e1e2e;color:#cdd6f4}'
      + '.toolbar{display:flex;justify-content:space-between;align-items:center;padding:0.5rem 1rem;background:#181825}'
      + 'button{padding:0.3rem 0.7rem;border-radius:4px;border:none;cursor:pointer;font-size:0.8rem}'
      + '.btn-save{background:#a6e3a1;color:#1e1e2e}.btn-revert{background:#f9e2af;color:#1e1e2e}'
      + '.btn-close{background:#45475a;color:#cdd6f4}'
      + '.meta{padding:0.3rem 1rem;font-size:0.75rem;color:#6c7086}'
      + 'textarea{width:calc(100% - 2rem);height:calc(100vh - 100px);margin:0.5rem 1rem;padding:0.75rem;background:#11111b;color:#cdd6f4;border:1px solid #313244;border-radius:4px;font-family:monospace;font-size:0.82rem;resize:none;box-sizing:border-box}'
      + '</style></head><body>'
      + '<div class="toolbar"><h3 style="margin:0;font-size:0.95rem">' + escapeHtml(label) + '</h3><div>'
      + '<button class="btn-revert" id="revertBtn">恢复备份</button> '
      + '<button class="btn-save" id="saveBtn">保存</button> '
      + '<button class="btn-close" onclick="window.close()">关闭</button>'
      + '</div></div><div class="meta" id="meta">加载中…</div><textarea id="editor"></textarea>'
      + '</body></html>'
    );
    w.document.close();

    // 绑定按钮事件并加载内容
    var doc = w.document;
    var meta = doc.getElementById('meta');
    var editor = doc.getElementById('editor');
    editor.value = '加载中…';

    var xhr = new XMLHttpRequest();
    xhr.open('GET', baseUrl + '/api/console/prompts/' + encodeURIComponent(phase));
    xhr.onload = function() {
      if (xhr.status === 200) {
        var d = JSON.parse(xhr.responseText);
        editor.value = d.content || '';
        meta.textContent = (d.filename || '') + ' (' + (d.size_bytes || 0) + ' bytes)';
      } else { editor.value = '加载失败: ' + xhr.status; }
    };
    xhr.onerror = function() { editor.value = '网络错误，无法加载提示词'; };
    xhr.send();

    doc.getElementById('saveBtn').onclick = function() {
      var xhr2 = new XMLHttpRequest();
      xhr2.open('POST', baseUrl + '/api/console/prompts/' + encodeURIComponent(phase));
      xhr2.setRequestHeader('Content-Type', 'application/json');
      xhr2.onload = function() {
        if (xhr2.status === 200) { alert('已保存'); }
        else { alert('保存失败: ' + xhr2.status); }
      };
      xhr2.onerror = function() { alert('网络错误'); };
      xhr2.send(JSON.stringify({content: editor.value}));
    };

    doc.getElementById('revertBtn').onclick = function() {
      showConfirm('确定恢复备份？', function() {
      var xhr3 = new XMLHttpRequest();
      xhr3.open('POST', baseUrl + '/api/console/prompts/' + encodeURIComponent(phase) + '/revert');
      xhr3.onload = function() {
        if (xhr3.status === 200) {
          var d = JSON.parse(xhr3.responseText);
          editor.value = d.content || '';
          meta.textContent = (d.filename || '') + ' (已恢复)';
        } else { alert('恢复失败: ' + xhr3.status); }
      };
      xhr3.onerror = function() { alert('网络错误'); };
      xhr3.send();
    });
  };
  }

  // ---------- 提示词面板（生成页直接访问）----------
  var _promptsLoaded = false;

  function bindPromptsPanel() {
    var toggle = $('prompts-panel-toggle');
    var body = $('prompts-body');
    var chevron = $('prompts-chevron');
    if (!toggle || !body) return;
    toggle.addEventListener('click', function() {
      if (body.style.display === 'none' || !body.style.display) {
        body.style.display = '';
        if (chevron) chevron.textContent = '收起 ⌃';
        if (!_promptsLoaded) loadPromptsList();
      } else {
        body.style.display = 'none';
        if (chevron) chevron.textContent = '展开 ⌵';
      }
    });
  }

  async function loadPromptsList() {
    var list = $('prompts-list');
    var summary = $('prompts-summary');
    if (!list) return;
    list.innerHTML = '<span class="inbox-meta">加载中…</span>';
    try {
      var data = await api('/api/console/prompts');
      _promptsLoaded = true;
      var items = data.prompts || [];
      if (summary) summary.textContent = items.length + ' 个模板';
      if (items.length === 0) {
        list.innerHTML = '<div class="inbox-meta">暂无提示词模板</div>';
        return;
      }
      list.innerHTML = items.map(function(p) {
        var statusIcon = p.exists ? '✓' : '✕';
        var size = p.size_bytes != null ? ' (' + p.size_bytes + ' bytes)' : '';
        return '<div class="card-glass" style="display:flex;align-items:center;gap:0.75rem;padding:0.6rem 1rem;margin-bottom:0.4rem;cursor:pointer" data-prompt-phase="' + escapeHtml(p.phase) + '" data-prompt-label="' + escapeHtml(p.label) + '">'
          + '<span>' + statusIcon + '</span>'
          + '<span style="flex:1;font-weight:600">' + escapeHtml(p.label) + '</span>'
          + '<span class="inbox-meta" style="font-size:0.75rem">' + escapeHtml(p.filename || '') + size + '</span>'
          + '<button class="btn-primary tiny" data-prompt-open="' + escapeHtml(p.phase) + '">编辑</button>'
          + '</div>';
      }).join('');
      // 点击整行或按钮都打开编辑器
      list.querySelectorAll('[data-prompt-phase]').forEach(function(row) {
        row.addEventListener('click', function(e) {
          if (e.target.closest('button')) return;
          openPromptWindow(row.dataset.promptPhase, row.dataset.promptLabel);
        });
      });
      list.querySelectorAll('[data-prompt-open]').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
          e.stopPropagation();
          openPromptWindow(btn.dataset.promptOpen, btn.closest('[data-prompt-label]')?.dataset?.promptLabel || btn.dataset.promptOpen);
        });
      });
    } catch (err) {
      list.innerHTML = '<div class="inbox-meta" style="color:var(--danger)">加载失败：' + err.message + '</div>';
    }
  }

  async function deleteStoryFromDetail(storyId) {
    try {
      const data = await api('/api/stories/' + storyId, { method: 'DELETE' });
      toast(data.message || '已删除', 'success');
      loadInbox();
      loadOverview();
    } catch (err) {
      toast('删除失败：' + err.message, 'error');
    }
  }

  // ───────────── 长篇小说 ─────────────
  var _lnActiveBookId = null;
  var _lnPanels = {
    setup: {el: 'ln-setup-panel', load: function(){loadSetupPanel();}},
    outline: {el: 'ln-outline-panel', load: function(){loadOutlinePanel();}},
    writing: {el: 'ln-writing-panel', load: function(){loadWritingWorkbench();}},
    benchmark: {el: 'ln-benchmark-panel', load: function(){loadBenchmarkPanel();}},
    tracking: {el: 'ln-tracking-panel', load: function(){loadTrackingPanel();}},
    pipeline: {el: 'ln-pipeline-panel', load: function(){loadPipelinePanel();}},
  };

  function bindLongNovel() {
    ensureWritingPanelLayout();
    $('ln-btn-new-book').addEventListener('click', function() { $('ln-new-book-modal').style.display = 'flex'; });
    $('ln-new-book-cancel').addEventListener('click', function() { $('ln-new-book-modal').style.display = 'none'; });
    $('ln-new-book-confirm').addEventListener('click', createNewBook);
    $('ln-btn-ai-suggest').addEventListener('click', aiSuggestBooks);
    $('ln-btn-refresh-books').addEventListener('click', loadBookList);
    _lnBindAutopilotWritingControls();
    $('ln-btn-back-library').addEventListener('click', function() {

      hideStepControls();
      _stopGlobalProgressPolling();
      _lnActiveBookId = null;

      $('ln-library-view').style.display = '';

      $('ln-book-workspace').style.display = 'none';

      loadBookList();

    });
    $$('[data-ln-sub]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        $$('[data-ln-sub]').forEach(function(b) { b.className = 'ghost tiny'; });
        btn.className = 'btn-primary tiny';
        var sub = btn.dataset.lnSub;
        if (sub !== 'writing') hideStepControls();
        // Hide all panels, show target
        Object.keys(_lnPanels).forEach(function(k) {
          var el = $(_lnPanels[k].el);
          if (el) el.style.display = (k === sub) ? '' : 'none';
        });
        // Load panel content
        var panel = _lnPanels[sub];
        if (panel && panel.load) panel.load();
        _lnRestoreAutopilotMonitor();
      });
    });
    // Writing workbench buttons are bound inside ensureWritingPanelLayout()
    // because they are created dynamically.
    var extendBtn = $('ln-btn-extend-chapters');
    if (extendBtn) extendBtn.addEventListener('click', _lnExtendChapters);
    var trackingRefreshBtn = $('ln-tracking-refresh');
    if (trackingRefreshBtn) trackingRefreshBtn.addEventListener('click', loadTrackingPanel);
    var benchmarkRefreshBtn = $('ln-benchmark-refresh');
    if (benchmarkRefreshBtn) benchmarkRefreshBtn.addEventListener('click', loadBenchmarkPanel);

    // Step-by-step writing buttons

    ['draft','expand','polish','deslop','review','finalize'].forEach(function(s) {

      var btn = document.getElementById('ln-step-btn-' + s);

      if (btn) {

        btn.addEventListener('click', function() { runStep(s); });

      }

    });

    // L0 phase chip clicks — show content or status for ALL phases
    var setupStrip = document.getElementById('ln-setup-strip');
    if (setupStrip) setupStrip.addEventListener('click', async function(e) {
      var chip = e.target.closest('[data-ln-phase]');
      if (!chip) return;

      // Don't intercept button clicks — their own handlers deal with them
      if (e.target.closest('button') || e.target.closest('[data-ln-retry]') || e.target.closest('[data-ln-prompt-view]') || e.target.closest('[data-ln-run]')) {
        return;
      }
      await _lnShowSetupChipPreview(chip.dataset.lnPhase);
    });
    // Delegate file clicks from directory listings (avoids inline onclick in IIFE)
    var previewContentEl = document.getElementById('ln-setup-preview-content');
    if (previewContentEl) previewContentEl.addEventListener('click', function(e) {
      var el = e.target.closest('[data-ln-load-file]');
      if (el) _lnLoadFile(el.dataset.lnLoadFile);
    });
    var outlinePhaseStrip = document.getElementById('ln-outline-phase-strip');
    if (outlinePhaseStrip) outlinePhaseStrip.addEventListener('click', async function(e) {
      var chip = e.target.closest('[data-ln-phase]');
      if (!chip) return;
      if (e.target.closest('button') || e.target.closest('[data-ln-retry]') || e.target.closest('[data-ln-prompt-view]') || e.target.closest('[data-ln-run]')) return;
      await _lnShowOutlineChipPreview(chip.dataset.lnPhase);
    });
    var outlinePreviewContentEl = document.getElementById('ln-outline-preview-content');
    if (outlinePreviewContentEl) outlinePreviewContentEl.addEventListener('click', function(e) {
      var el = e.target.closest('[data-ln-outline-file]');
      if (el) _lnLoadOutlineFile(el.dataset.lnOutlineFile);
    });
  }  // ── 书库 ──

  function _lnRenderOutlineChipFiles(phaseId, label, files, status) {
    var titleEl = document.getElementById('ln-outline-preview-title');
    var contentEl = document.getElementById('ln-outline-preview-content');
    if (!contentEl) return;
    if (!files.length) {
      contentEl.innerHTML = '<div class="empty" style="padding:0.5rem">阶段已完成，但未找到产出文件</div>';
      _lnSetOutlinePreviewAction('<button class="btn-warning tiny" data-ln-preview-retry="' + phaseId + '">重新生成「' + escapeHtml(label) + '」</button>');
      return;
    }
    var defaultIdx = 0;
    for (var i = 0; i < files.length; i++) {
      if (!files[i].is_index) { defaultIdx = i; break; }
    }
    var stIcon = status === 'done' ? '✓' : (status === 'cancelled' ? '' : '');
    if (titleEl) titleEl.textContent = stIcon + ' ' + label + ' · ' + files.length + ' 个文件';
    var listHtml = '<div class="ln-chip-filelist">' + files.map(function(f, idx) {
      var sizeKb = (f.bytes / 1024).toFixed(1);
      var active = idx === defaultIdx ? ' active' : '';
      return '<button class="ln-chip-fileitem' + active + '" data-ln-file-rel="' + escapeHtml(f.path) + '" title="' + escapeHtml(f.path) + '">'
        + '' + escapeHtml(f.name)
        + '<span class="ln-chip-filesize">' + sizeKb + 'K</span>'
        + '</button>';
    }).join('') + '</div>';
    contentEl.classList.remove('empty');
    contentEl.innerHTML = listHtml + '<div class="ln-chip-fileview" id="ln-outline-chip-fileview"><div class="empty" style="padding:0.5rem">加载中…</div></div>';
    contentEl.querySelectorAll('[data-ln-file-rel]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        contentEl.querySelectorAll('.ln-chip-fileitem.active').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        _lnSetOutlinePreviewAction(
          _lnSetupFileActionsHtml(btn.dataset.lnFileRel) + ' '
          + '<button class="btn-warning tiny" data-ln-preview-retry="' + phaseId + '">重新生成「' + escapeHtml(label) + '」</button> '
          + '<button class="ghost tiny" data-ln-outline-preview-refresh="' + phaseId + '">↻ 刷新文件列表</button>'
        );
        _lnLoadChipFileInto(btn.dataset.lnFileRel, document.getElementById('ln-outline-chip-fileview'));
      });
    });
    _lnLoadChipFileInto(files[defaultIdx].path, document.getElementById('ln-outline-chip-fileview'));
    _lnSetOutlinePreviewAction(
      _lnSetupFileActionsHtml(files[defaultIdx].path) + ' '
      + '<button class="btn-warning tiny" data-ln-preview-retry="' + phaseId + '">重新生成「' + escapeHtml(label) + '」</button> '
      + '<button class="ghost tiny" data-ln-outline-preview-refresh="' + phaseId + '">↻ 刷新文件列表</button>'
    );
  }

  function _lnOpenBook(bookId) {
    _lnActiveBookId = bookId;
    _lnViewChapter = _lnRestoreWorkspaceChapter(bookId);
    _lnPersistWorkspaceState();
    $('ln-library-view').style.display = 'none';
    $('ln-book-workspace').style.display = '';
    // Default to setup (设定) tab
    $$('[data-ln-sub]').forEach(function(b) { b.className = 'ghost tiny'; });
    var setupTab = document.querySelector('[data-ln-sub="setup"]');
    if (setupTab) setupTab.className = 'btn-primary tiny';
    Object.keys(_lnPanels).forEach(function(k) {
      var el = $(_lnPanels[k].el);
      if (el) el.style.display = (k === 'setup') ? '' : 'none';
    });
    loadWritingWorkbench();
    loadSetupPanel();
    _lnRestoreAutopilotMonitor();
    _startGlobalProgressPolling();
  }

  function _lnBookStats(book) {
    var chapters = Array.isArray(book._chapters) ? book._chapters : [];
    var planned = Number(book.target_chapters || book.chapters_total || book.chapter_count || chapters.length || 0);
    if (chapters.length > planned) planned = chapters.length;
    var done = 0;
    var writing = 0;
    var outlined = 0;
    var words = Number(book.words_total || book.total_words || 0);
    if (chapters.length) {
      words = 0;
      chapters.forEach(function(ch) {
        var status = String(ch.status || '').toLowerCase();
        var actual = Number(ch.actual_words || ch.words || 0);
        words += actual;
        if (actual > 0 || ch.draft_path || ['done', 'final', 'completed', 'published', 'draft'].indexOf(status) >= 0) {
          done += 1;
        } else if (status === 'writing' || status === 'in_progress') {
          writing += 1;
        } else if (ch.outline_path || status === 'outline_only' || status === 'outlined') {
          outlined += 1;
        }
      });
    } else {
      done = Number(book.chapters_done || book.completed_chapters || book.finished_chapters || 0);
      if (!done && book.current_chapter) done = Math.max(0, Number(book.current_chapter) - (book.status === 'completed' ? 0 : 1));
      writing = book.status === 'writing' ? 1 : Number(book.chapters_writing || 0);
      outlined = Math.max(0, planned - done - writing);
    }
    var pending = Math.max(0, planned - done - writing - outlined);
    var pct = planned > 0 ? Math.min(100, Math.round(done / planned * 100)) : 0;
    return { planned: planned, done: done, writing: writing, outlined: outlined, pending: pending, words: words, pct: pct };
  }

  function _lnBookStatus(book, stats) {
    var status = String(book.status || '').toLowerCase();
    if (status === 'completed') return { label: '已完结', cls: 'is-completed' };
    if (status === 'paused') return { label: '暂停', cls: 'is-paused' };
    if (status === 'setup') return { label: '设定中', cls: 'is-setup' };
    if (stats && stats.writing > 0) return { label: '写作中', cls: 'is-writing' };
    if (status === 'writing') return { label: '连载中', cls: 'is-writing' };
    return { label: book.status || '未开始', cls: 'is-idle' };
  }

  function _lnBookDate(value) {
    if (!value) return '未更新';
    return String(value).replace('T', ' ').replace(/\.\d+Z?$/, '').slice(0, 16);
  }

  async function _lnEnrichBooks(books) {
    return Promise.all(books.map(async function(book) {
      try {
        var ch = await api('/api/long-novel/books/' + book.id + '/chapters');
        book._chapters = ch.chapters || [];
      } catch (_err) {
        book._chapters = [];
      }
      return book;
    }));
  }

  async function loadBookList() {
    var list = $('ln-book-list');
    if (!list) return;
    try {
      var data = await api('/api/long-novel/books');
      var books = await _lnEnrichBooks(data.books || []);
      var countEl = $('ln-library-count'); if (countEl) countEl.textContent = books.length;
      var libraryStats = books.reduce(function(acc, b) {
        var stats = _lnBookStats(b);
        var status = _lnBookStatus(b, stats);
        acc.done += stats.done;
        acc.planned += stats.planned;
        acc.words += stats.words;
        if (status.cls === 'is-writing') acc.writing += 1;
        return acc;
      }, { done: 0, planned: 0, words: 0, writing: 0 });
      var writingEl = $('ln-library-writing-count'); if (writingEl) writingEl.textContent = libraryStats.writing;
      var updatedEl = $('ln-library-updated'); if (updatedEl) updatedEl.textContent = '最后刷新 ' + _lnBookDate(new Date().toISOString());
      if (books.length === 0) {
        list.classList.add('empty');
        list.innerHTML = '<div class="ln-library-empty"><strong>暂无书籍</strong><span>点击右上角“新建书籍”开始第一本长篇。</span></div>';
        return;
      }
      list.classList.remove('empty');
      list.innerHTML = books.map(function(b) {
        var stats = _lnBookStats(b);
        var status = _lnBookStatus(b, stats);
        var title = b.title || ('未命名书籍 #' + b.id);
        var premise = (b.premise || '暂无题材简介。').trim();
        var genre = b.genre || '未分类';
        var targetWords = Number(b.target_words_per_chapter || 3000);
        var chapterText = stats.done + '/' + stats.planned;
        var progressWidth = Math.max(1, stats.pct);
        var outlineCount = stats.outlined || Math.max(0, stats.planned - stats.done - stats.writing);
        return '<article class="ln-book ln-book-card ' + status.cls + '" tabindex="0" role="button" data-ln-book-id="' + b.id + '">'
          + '<div class="ln-book-card-top">'
          + '<span class="ln-book-status"><i></i>' + escapeHtml(status.label) + '</span>'
          + '<span class="ln-book-updated">' + escapeHtml(_lnBookDate(b.updated_at || b.created_at)) + '</span>'
          + '</div>'
          + '<h3 class="ln-book-title">' + escapeHtml(title) + '</h3>'
          + '<div class="ln-book-genre">' + escapeHtml(genre) + '</div>'
          + '<p class="ln-book-premise">' + escapeHtml(premise.length > 132 ? premise.slice(0, 132) + '…' : premise) + '</p>'
          + '<div class="ln-book-metrics">'
          + '<div><span>章节</span><strong>' + escapeHtml(chapterText) + '</strong></div>'
          + '<div><span>字数</span><strong>' + escapeHtml(fmtNum(stats.words || 0)) + '</strong></div>'
          + '<div><span>目标</span><strong>' + escapeHtml(fmtNum(targetWords)) + '</strong></div>'
          + '</div>'
          + '<div class="ln-book-progress" aria-label="章节完成度">'
          + '<div class="ln-book-bar"><i style="width:' + progressWidth + '%"></i></div>'
          + '<span>' + stats.pct + '%</span>'
          + '</div>'
          + '<div class="ln-book-foot">'
          + '<span>成稿 ' + stats.done + ' · 写作 ' + stats.writing + ' · 细纲 ' + outlineCount + '</span>'
          + '<span>第 ' + (b.current_chapter || Math.min(stats.done + 1, stats.planned || 1)) + ' 章</span>'
          + '</div>'
          + '<div class="ln-book-actions">'
          + '<span class="ln-book-open">进入工作台</span>'
          + '<button class="btn-danger tiny" data-ln-del="' + b.id + '" title="删除书籍">删除</button>'
          + '</div>'
          + '</article>';
      }).join('');
      list.querySelectorAll('[data-ln-del]').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
          e.stopPropagation();
          deleteBookById(parseInt(btn.dataset.lnDel));
        });
      });
      list.querySelectorAll('[data-ln-book-id]').forEach(function(card) {
        card.addEventListener('click', function() {
          _lnOpenBook(parseInt(card.dataset.lnBookId));
        });
        card.addEventListener('keydown', function(e) {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            _lnOpenBook(parseInt(card.dataset.lnBookId));
          }
        });
      });
    } catch (err) {
      list.classList.add('empty');
      list.innerHTML = '<div class="inbox-meta" style="color:var(--danger)">加载失败：' + err.message + '</div>';
    }
  }

  async function createNewBook() {
    var title = ($('ln-new-title').value || '').trim();
    if (!title) { toast('请输入书名', 'error'); return; }
    try {
      var data = await api('/api/long-novel/books', { method: 'POST', body: {
        title: title,
        genre: ($('ln-new-genre').value || '').trim(),
        premise: ($('ln-new-premise').value || '').trim(),
        target_chapters: parseInt($('ln-new-chapters').value) || 30,
        target_words_per_chapter: parseInt($('ln-new-words').value) || 3000,
      }});
      toast(data.message || '创建成功，开始创作准备...', 'success');
      $('ln-new-book-modal').style.display = 'none';
      $('ln-new-title').value = '';
      $('ln-new-genre').value = '';
      $('ln-new-premise').value = '';
      _lnActiveBookId = data.book_id;
      // Navigate to workspace
      $('ln-library-view').style.display = 'none';
      $('ln-book-workspace').style.display = '';
      $('ln-ws-book-title').textContent = '' + escapeHtml(title);
      $('ln-ws-progress').textContent = '准备开书设定...';
      // Activate setup tab and auto-run setup
      $$('[data-ln-sub]').forEach(function(b) { b.className = 'ghost tiny'; });
      var setupTab = document.querySelector('[data-ln-sub="setup"]');
      if (setupTab) setupTab.className = 'btn-primary tiny';
        Object.keys(_lnPanels).forEach(function(k) {
          var el = $(_lnPanels[k].el);
          if (el) el.style.display = (k === 'setup') ? '' : 'none';
        });
      // 不再自动跑设定；让用户选择「全自动 / 手动」。
      _lnShowSetupChoice(title);
    } catch (err) { toast('创建失败：' + err.message, 'error'); }
  }

  // ── 开书后：全自动 / 手动 选择 + autopilot 监控 ──
  var _lnAutopilotTimer = null;
  var _lnAutopilotChapterCount = 0;

  function _lnAutopilotStageDefs() {
    // 9 setup stages + the optional writing confirmation/execution stage.
    return _lnAllSetupPhases().concat([{ id: 'finalize', label: '入库' }, { id: 'writing', label: '正文' }]);
  }

  function _lnAutopilotPendingKey(bookId) {
    return 'anp-ln-autopilot-pending-chapters:' + bookId;
  }

  function _lnAutopilotPendingRangeKey(bookId) {
    return 'anp-ln-autopilot-pending-range:' + bookId;
  }

  function _lnSetAutopilotPendingChapters(count) {
    if (!_lnActiveBookId) return;
    count = Math.max(0, Number(count || 0));
    try {
      if (count > 0) localStorage.setItem(_lnAutopilotPendingKey(_lnActiveBookId), String(count));
      else {
        localStorage.removeItem(_lnAutopilotPendingKey(_lnActiveBookId));
        localStorage.removeItem(_lnAutopilotPendingRangeKey(_lnActiveBookId));
      }
    } catch (_) {}
  }

  function _lnSetAutopilotPendingRange(start, end) {
    if (!_lnActiveBookId) return;
    start = parseInt(start, 10);
    end = parseInt(end, 10);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 1 || end < start) {
      _lnSetAutopilotPendingChapters(0);
      return;
    }
    try {
      localStorage.setItem(_lnAutopilotPendingKey(_lnActiveBookId), String(end - start + 1));
      localStorage.setItem(_lnAutopilotPendingRangeKey(_lnActiveBookId), JSON.stringify({ start: start, end: end }));
    } catch (_) {}
  }

  function _lnGetAutopilotPendingRange() {
    if (!_lnActiveBookId) return null;
    try {
      var raw = localStorage.getItem(_lnAutopilotPendingRangeKey(_lnActiveBookId));
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      var start = parseInt(parsed.start, 10);
      var end = parseInt(parsed.end, 10);
      if (Number.isFinite(start) && Number.isFinite(end) && start > 0 && end >= start) {
        return { start: start, end: end, count: end - start + 1 };
      }
    } catch (_) {}
    return null;
  }

  function _lnGetAutopilotPendingChapters() {
    if (!_lnActiveBookId) return 0;
    try {
      var range = _lnGetAutopilotPendingRange();
      if (range) return range.count;
      var raw = localStorage.getItem(_lnAutopilotPendingKey(_lnActiveBookId));
      var n = parseInt(raw || '0', 10);
      return Number.isFinite(n) && n > 0 ? n : 0;
    } catch (_) {
      return 0;
    }
  }

  function _lnShowSetupChoice(title) {
    var titleEl = $('ln-choice-title');
    if (titleEl) titleEl.textContent = title || '';
    ['ln-setup-strip', 'ln-setup-preview'].forEach(function(id) {
      var el = $(id); if (el) el.style.display = 'none';
    });
    var monitor = $('ln-autopilot-monitor');
    if (monitor) monitor.style.display = '';
    _lnRenderAutopilotMonitor({ state: 'idle', detail: '先选择正文范围；全自动会完成前置内容，并在正文前等待确认。' });
    _lnRefreshAutopilotRangeDefaults({ force: true });
    var choice = $('ln-setup-choice');
    if (choice) choice.style.display = '';
    var autoBtn = $('ln-btn-autopilot');
    var manualBtn = $('ln-btn-manual-setup');
    if (autoBtn) autoBtn.onclick = _lnStartAutopilotFromSetupChoice;
    if (manualBtn) manualBtn.onclick = _lnChooseManualSetup;
  }

  function _lnChooseManualSetup() {
    var choice = $('ln-setup-choice'); if (choice) choice.style.display = 'none';
    var monitor = $('ln-autopilot-monitor'); if (monitor) monitor.style.display = 'none';
    loadSetupPanel();  // 渲染逐 phase chip（带「运行」按钮）
    toast('已切换到手动模式：在下方每一步点开后「运行」', 'info');
  }

  function _lnAutopilotMaxRevisions() {
    return 0;
  }

  function _lnChapterHasAutopilotDraft(ch) {
    if (!ch) return false;
    var status = String(ch.status || '').toLowerCase();
    return !!ch.draft_path
      || Number(ch.actual_words || 0) > 0
      || ['draft', 'reviewed', 'published', 'completed', 'final', 'finalized', 'done', 'needs_human'].indexOf(status) >= 0;
  }

  function _lnFirstUnwrittenChapterNumber(chapters) {
    var sorted = (chapters || []).slice().sort(function(a, b) {
      return Number(a.chapter_number || 0) - Number(b.chapter_number || 0);
    });
    for (var i = 0; i < sorted.length; i++) {
      if (!_lnChapterHasAutopilotDraft(sorted[i])) return Number(sorted[i].chapter_number || 0);
    }
    return 0;
  }

  function _lnRangeLabel(range) {
    if (!range) return '';
    if (range.start && range.end) {
      return range.start === range.end ? ('第' + range.start + '章') : ('第' + range.start + '-' + range.end + '章');
    }
    return (range.count || 0) + ' 章';
  }

  function _lnNormalizeAutopilotRangeInputs() {
    var startEl = $('ln-autopilot-start-chapter');
    var endEl = $('ln-autopilot-end-chapter');
    if (!startEl || !endEl) return;
    var start = parseInt(startEl.value, 10);
    var end = parseInt(endEl.value, 10);
    if (!Number.isFinite(start) || start < 1) start = 1;
    if (!Number.isFinite(end) || end < start) end = start;
    startEl.value = String(start);
    endEl.value = String(end);
  }

  function _lnSetAutopilotRangeHint(message, kind) {
    var hint = $('ln-autopilot-range-hint');
    if (!hint) return;
    hint.textContent = message || '从最早未写章节开始连续生成';
    hint.style.color = kind === 'error' ? 'var(--danger)' : (kind === 'ok' ? 'var(--success)' : 'var(--muted)');
  }

  async function _lnRefreshAutopilotRangeDefaults(options) {
    options = options || {};
    if (!_lnActiveBookId) return;
    var startEl = $('ln-autopilot-start-chapter');
    var endEl = $('ln-autopilot-end-chapter');
    var btn = $('ln-btn-autopilot-write-range');
    if (!startEl || !endEl) return;
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = data.book || {};
      var chapters = Array.isArray(book.chapters) ? book.chapters : [];
      var maxChapter = Math.max(
        Number(book.target_chapters || 0),
        chapters.reduce(function(max, ch) { return Math.max(max, Number(ch.chapter_number || 0)); }, 0),
        1
      );
      var earliest = chapters.length ? _lnFirstUnwrittenChapterNumber(chapters) : 1;
      startEl.min = '1';
      endEl.min = '1';
      startEl.max = String(maxChapter);
      endEl.max = String(maxChapter);
      if (!earliest) {
        _lnSetAutopilotRangeHint('所有章节已有正文；如需继续，请先在大纲里追加章节。', 'ok');
        if (btn) btn.disabled = true;
        return;
      }
      if (btn) btn.disabled = false;
      if (options.force || !startEl.value || !endEl.value) {
        startEl.value = String(earliest);
        endEl.value = String(earliest);
      }
      _lnNormalizeAutopilotRangeInputs();
      var chosenStart = parseInt(startEl.value, 10);
      if (chosenStart !== earliest) {
        _lnSetAutopilotRangeHint('需要从第' + earliest + '章开始连续写，不能跳到第' + chosenStart + '章。', 'error');
      } else {
        _lnSetAutopilotRangeHint('将从第' + earliest + '章开始连续写，方便追踪伏笔和上下文。', 'ok');
      }
    } catch (_err) {
      _lnSetAutopilotRangeHint('章节范围稍后会在启动前再次校验。', 'warn');
    }
  }

  async function _lnReadAutopilotWritingRange() {
    if (!_lnActiveBookId) throw new Error('请先选择一本书');
    _lnNormalizeAutopilotRangeInputs();
    var start = parseInt(($('ln-autopilot-start-chapter') || {}).value, 10);
    var end = parseInt(($('ln-autopilot-end-chapter') || {}).value, 10);
    if (!Number.isFinite(start) || start < 1) throw new Error('正文起始章必须大于 0');
    if (!Number.isFinite(end) || end < start) throw new Error('正文结束章不能小于起始章');

    var data = await api('/api/long-novel/books/' + _lnActiveBookId);
    var book = data.book || {};
    var chapters = Array.isArray(book.chapters) ? book.chapters : [];
    var target = Number(book.target_chapters || chapters.length || end || 1);
    if (!chapters.length) {
      if (start !== 1) throw new Error('需要从第1章开始连续写，不能跳到第' + start + '章，否则追踪/伏笔会断。');
      if (end > target) throw new Error('这本书当前计划只有 ' + target + ' 章，请先调整目标章节数或生成章节细纲。');
      return { start: start, end: end, count: end - start + 1 };
    }

    var byNumber = {};
    chapters.forEach(function(ch) { byNumber[Number(ch.chapter_number || 0)] = ch; });
    var earliest = _lnFirstUnwrittenChapterNumber(chapters);
    if (!earliest) throw new Error('所有章节已有正文；如需继续，请先在大纲里追加章节。');
    if (start !== earliest) {
      throw new Error('需要从第' + earliest + '章开始连续写，不能跳到第' + start + '章，否则追踪/伏笔会断。');
    }
    for (var n = start; n <= end; n++) {
      if (!byNumber[n]) throw new Error('章节队列缺少第' + n + '章，请先生成章节细纲并入库。');
      if (_lnChapterHasAutopilotDraft(byNumber[n])) {
        throw new Error('第' + n + '章已有正文，请从最早未写章节连续生成。');
      }
    }
    return { start: start, end: end, count: end - start + 1 };
  }

  function _lnBindAutopilotWritingControls() {
    var btn = $('ln-btn-autopilot-write-range');
    var startEl = $('ln-autopilot-start-chapter');
    var endEl = $('ln-autopilot-end-chapter');
    if (btn && btn.dataset.bound !== '1') {
      btn.dataset.bound = '1';
      btn.addEventListener('click', _lnStartAutopilotWritingRange);
    }
    [startEl, endEl].forEach(function(el) {
      if (!el || el.dataset.bound === '1') return;
      el.dataset.bound = '1';
      el.addEventListener('input', function() {
        _lnNormalizeAutopilotRangeInputs();
        _lnRefreshAutopilotRangeDefaults({ force: false });
      });
    });
  }

  async function _lnStartAutopilotWritingRange() {
    try {
      var range = await _lnReadAutopilotWritingRange();
      var bookData = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = bookData.book || {};
      var needsSetup = String(book.status || '') === 'setup' || !Array.isArray(book.chapters) || !book.chapters.length;
      await _lnStartAutopilot({
        chapterCount: range.count,
        chapterStart: range.start,
        chapterEnd: range.end,
        maxRevisions: _lnAutopilotMaxRevisions(),
        pauseBeforeWriting: needsSetup,
        detail: needsSetup
          ? '启动中：先完成开书设定，随后停在正文确认。'
          : '启动中：正在全自动写正文 ' + _lnRangeLabel(range) + '…',
        successToast: needsSetup
          ? '已启动全自动：完成前置内容后会停在正文确认'
          : '已启动全自动写正文 ' + _lnRangeLabel(range),
      });
    } catch (err) {
      _lnSetAutopilotRangeHint(err.message, 'error');
      toast(err.message, 'error');
    }
  }

  async function _lnStartAutopilotFromSetupChoice() {
    try {
      var range = await _lnReadAutopilotWritingRange();
      await _lnStartAutopilot({
        chapterCount: range.count,
        chapterStart: range.start,
        chapterEnd: range.end,
        maxRevisions: _lnAutopilotMaxRevisions(),
        pauseBeforeWriting: true,
        detail: '启动中：先完成开书设定，随后停在正文确认。',
        successToast: '已启动全自动：完成前置内容后会停在正文确认',
      });
    } catch (err) {
      _lnSetAutopilotRangeHint(err.message, 'error');
      toast(err.message, 'error');
    }
  }

  async function _lnStartAutopilot(options) {
    options = (options && !options.type) ? options : {};
    if (!_lnActiveBookId) { toast('请先选择一本书', 'error'); return; }
    var chapterStart = options.chapterStart != null ? parseInt(options.chapterStart, 10) : null;
    var chapterEnd = options.chapterEnd != null ? parseInt(options.chapterEnd, 10) : null;
    var hasRange = Number.isFinite(chapterStart) && Number.isFinite(chapterEnd) && chapterStart > 0 && chapterEnd >= chapterStart;
    var chapterCount = options.chapterCount != null
      ? Number(options.chapterCount)
      : (hasRange ? (chapterEnd - chapterStart + 1) : 0);
    if (isNaN(chapterCount) || chapterCount < 0) chapterCount = 0;
    _lnAutopilotChapterCount = chapterCount;
    var pauseBeforeWriting = options.pauseBeforeWriting !== false && chapterCount > 0;
    var requestChapterCount = pauseBeforeWriting ? 0 : chapterCount;
    if (pauseBeforeWriting) {
      if (hasRange) _lnSetAutopilotPendingRange(chapterStart, chapterEnd);
      else _lnSetAutopilotPendingChapters(chapterCount);
    } else if (requestChapterCount > 0) {
      _lnSetAutopilotPendingChapters(0);
    }
    var choice = $('ln-setup-choice'); if (choice) choice.style.display = 'none';
    var monitor = $('ln-autopilot-monitor'); if (monitor) monitor.style.display = '';
    var cancelBtn = $('ln-btn-autopilot-cancel');
    if (cancelBtn) cancelBtn.onclick = _lnCancelAutopilot;
    _lnRenderAutopilotMonitor({ state: 'running', detail: options.detail || '启动中…' });
    try {
      var body = { chapter_count: requestChapterCount };
      if (!pauseBeforeWriting && hasRange && requestChapterCount > 0) {
        body.chapter_start = chapterStart;
        body.chapter_end = chapterEnd;
      }
      await api('/api/long-novel/books/' + _lnActiveBookId + '/autopilot/start', {
        method: 'POST',
        body: body,
      });
      if (options.successToast) toast(options.successToast, 'info');
      _lnStartAutopilotPolling();
    } catch (err) {
      toast('启动全自动失败：' + err.message, 'error');
      _lnRenderAutopilotMonitor({ state: 'error', detail: err.message || '启动失败' });
    }
  }

  async function _lnConfirmAutopilotWriting() {
    try {
      var range = await _lnReadAutopilotWritingRange();
      await _lnStartAutopilot({
        chapterCount: range.count,
        chapterStart: range.start,
        chapterEnd: range.end,
        maxRevisions: _lnAutopilotMaxRevisions(),
        pauseBeforeWriting: false,
        detail: '已确认前置内容，正在启动正文自动写作 ' + _lnRangeLabel(range) + '…',
        successToast: '已确认，开始全自动写正文 ' + _lnRangeLabel(range),
      });
    } catch (err) {
      _lnSetAutopilotRangeHint(err.message, 'error');
      toast(err.message, 'error');
    }
  }

  function _lnSkipAutopilotWriting() {
    _lnSetAutopilotPendingChapters(0);
    _lnRenderAutopilotMonitor({
      state: 'done',
      completed: _lnAutopilotStageDefs().filter(function(s) { return s.id !== 'writing'; }).map(function(s) { return s.id; }),
      detail: '已保留开书设定，正文稍后可在「全自动生成」里继续。',
      updated_at: '',
    });
    toast('已停在正文前，稍后可在「全自动生成」里继续写。', 'info');
  }

  async function _lnCancelAutopilot() {
    if (!_lnActiveBookId) return;
    if (!await showConfirmAsync('暂停全自动生成？已生成的步骤会保留，重新点「全自动」会从中断处继续。')) return;
    try {
      await api('/api/long-novel/books/' + _lnActiveBookId + '/cancel', { method: 'POST' });
      toast('已发送暂停信号，将在当前步骤完成后停止', 'warning');
    } catch (err) { toast('暂停失败：' + err.message, 'error'); }
  }

  function _lnStartAutopilotPolling() {
    if (_lnAutopilotTimer) clearInterval(_lnAutopilotTimer);
    _lnPollAutopilot();
    _lnAutopilotTimer = setInterval(_lnPollAutopilot, 2000);
  }

  function _lnStopAutopilotPolling() {
    if (_lnAutopilotTimer) { clearInterval(_lnAutopilotTimer); _lnAutopilotTimer = null; }
  }

  var _lnAutopilotWorkbenchRefreshAt = 0;
  function _lnRefreshWritingWorkbenchDuringAutopilot(data) {
    if (!data || data.state !== 'running' || data.phase !== 'writing') return;
    if (!document.getElementById('ln-chapter-list')) return;
    var now = Date.now();
    if (now - _lnAutopilotWorkbenchRefreshAt < 2500) return;
    _lnAutopilotWorkbenchRefreshAt = now;
    try {
      var refreshed = loadWritingWorkbench();
      if (refreshed && typeof refreshed.catch === 'function') refreshed.catch(function() {});
    } catch (_e) {}
  }

  async function _lnPollAutopilot() {
    if (!_lnActiveBookId) { _lnStopAutopilotPolling(); return; }
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId + '/autopilot/status');
      _lnRenderAutopilotMonitor(data);
      _lnRefreshWritingWorkbenchDuringAutopilot(data);
      if (['done', 'error', 'cancelled', 'idle'].indexOf(data.state) >= 0) {
        _lnStopAutopilotPolling();
        if (data.state === 'done') {
          var w = data.writing;
          if (w && w.total) {
            _lnSetAutopilotPendingChapters(0);
            var msg = '全自动完成！正文已写 ' + w.total + ' 章';
            msg += '，审核分数仅供参考';
            toast(msg, 'success');
          } else if (_lnGetAutopilotPendingChapters() > 0) {
            toast('前置内容已生成完成，请确认后再开始全自动写正文。', 'success');
          } else {
            toast('开书设定全部完成！可以到「正文」开始写作了', 'success');
          }
          if (typeof loadBookOverview === 'function') { try { loadBookOverview(); } catch (e) {} }
          if (typeof loadWritingWorkbench === 'function') { try { loadWritingWorkbench(); } catch (e) {} }
        } else if (data.state === 'error') {
          toast('全自动中断：' + (data.detail || '未知错误') + '（可重新开始，已完成步骤会自动跳过）', 'error');
        } else if (data.state === 'cancelled') {
          toast('已暂停。重新点「全自动」会从中断处继续', 'warning');
        }
      }
    } catch (err) { /* 瞬时轮询错误，继续重试 */ }
  }

  async function _lnRestoreAutopilotMonitor() {
    if (!_lnActiveBookId) return;
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId + '/autopilot/status');
      if (!data || data.state === 'idle') {
        var setupPanel = $('ln-setup-panel');
        if (setupPanel && setupPanel.style.display !== 'none') {
          var idleMonitor = $('ln-autopilot-monitor'); if (idleMonitor) idleMonitor.style.display = '';
          _lnRenderAutopilotMonitor({ state: 'idle', detail: '选择正文范围后，可以直接全自动写正文；若前置内容未完成，会先生成并在正文前等待确认。' });
          _lnRefreshAutopilotRangeDefaults({ force: false });
        }
        return;
      }
      var choice = $('ln-setup-choice'); if (choice) choice.style.display = 'none';
      var monitor = $('ln-autopilot-monitor'); if (monitor) monitor.style.display = '';
      _lnRenderAutopilotMonitor(data);
      if (data.state === 'running') _lnStartAutopilotPolling();
    } catch (_err) {
      /* The manual setup panel remains usable if the progress snapshot cannot be read. */
    }
  }

  function _lnRenderAutopilotMonitor(data) {
    data = data || {};
    var stagesEl = $('ln-autopilot-stages');
    var detailEl = $('ln-autopilot-detail');
    var confirmEl = $('ln-autopilot-confirm');
    var titleEl = $('ln-autopilot-title');
    var cancelBtn = $('ln-btn-autopilot-cancel');
    var defs = _lnAutopilotStageDefs();
    var completed = data.completed || [];
    var pendingRange = _lnGetAutopilotPendingRange();
    var pendingChapters = pendingRange ? pendingRange.count : _lnGetAutopilotPendingChapters();
    var pendingLabel = pendingRange ? _lnRangeLabel(pendingRange) : (pendingChapters + ' 章');
    var awaitingWritingConfirm = data.state === 'done' && !(data.writing && data.writing.total) && pendingChapters > 0;
    var current = awaitingWritingConfirm ? 'writing' : (data.stage || '');
    var failedAt = data.failed_at || '';
    if (titleEl) {
      titleEl.textContent = awaitingWritingConfirm ? '等待确认正文写作'
        : data.state === 'done' ? '全自动生成完成'
        : data.state === 'error' ? '全自动生成失败'
        : data.state === 'cancelled' ? '全自动生成已暂停'
        : data.state === 'idle' ? '全自动生成'
        : '全自动生成中';
    }
    if (cancelBtn) {
      cancelBtn.style.display = (data.state === 'done' || data.state === 'idle') ? 'none' : '';
      if (awaitingWritingConfirm) {
        cancelBtn.style.display = '';
        cancelBtn.textContent = '确认写正文';
        cancelBtn.className = 'btn-primary tiny';
        cancelBtn.onclick = _lnConfirmAutopilotWriting;
      } else if (data.state === 'running') {
        cancelBtn.textContent = '暂停';
        cancelBtn.className = 'ghost tiny';
        cancelBtn.onclick = _lnCancelAutopilot;
      } else {
        cancelBtn.textContent = '继续全自动';
        cancelBtn.className = 'btn-primary tiny';
        cancelBtn.onclick = _lnStartAutopilot;
      }
    }
    var rangeBtn = $('ln-btn-autopilot-write-range');
    var startEl = $('ln-autopilot-start-chapter');
    var endEl = $('ln-autopilot-end-chapter');
    var rangeBusy = data.state === 'running';
    if (rangeBtn) rangeBtn.disabled = rangeBusy;
    if (startEl) startEl.disabled = rangeBusy;
    if (endEl) endEl.disabled = rangeBusy;
    if (data.state !== 'running') _lnRefreshAutopilotRangeDefaults({ force: false });
    if (stagesEl) {
      stagesEl.innerHTML = defs.map(function(s) {
        var state = '待处理', color = 'var(--muted)';
        if (completed.indexOf(s.id) >= 0) { state = '完成'; color = 'var(--success)'; }
        if (s.id === 'writing' && data.writing && data.writing.total && data.state === 'done') { state = '完成'; color = 'var(--success)'; }
        if (s.id === 'writing' && data.phase === 'writing' && data.state === 'running') { state = '进行中'; color = 'var(--primary)'; }
        if (s.id === 'writing' && awaitingWritingConfirm) { state = '待确认'; color = 'var(--warning)'; }
        if (s.id === failedAt) { state = '失败'; color = 'var(--danger)'; }
        else if (s.id === current && data.state === 'running') { state = '进行中'; color = 'var(--primary)'; }
        else if (s.id === current && data.state === 'cancelled') { state = '已暂停'; color = 'var(--warning)'; }
        return '<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.75rem;color:' + color + ';border:1px solid ' + color + '">' + escapeHtml(s.label) + ' · ' + state + '</span>';
      }).join('');
    }
    _lnRenderAutopilotWriting(data.writing, data.state, data.detail);
    if (detailEl) {
      var head = data.state === 'done' ? '全部完成'
        : data.state === 'error' ? '失败'
        : data.state === 'cancelled' ? '已暂停'
        : data.state === 'idle' ? '待开始'
        : (data.label ? ('正在：' + data.label) : '准备中…');
      var detail = data.detail ? ('\n' + String(data.detail).substring(0, 2000)) : '';
      if (awaitingWritingConfirm) {
        head = '正文待确认';
        detail = '\n前置设定、大纲、卷纲、章节细纲已完成。请先检查上方「设定 / 大纲」内容，确认后再自动写正文 ' + pendingLabel + '。';
      }
      detailEl.textContent = '[' + (data.updated_at || '') + '] ' + head + detail;
    }
    if (confirmEl) {
      if (awaitingWritingConfirm) {
        confirmEl.style.display = '';
        confirmEl.innerHTML = ''
          + '<div style="display:flex;align-items:center;justify-content:space-between;gap:0.75rem;flex-wrap:wrap;padding:0.65rem 0.75rem;border:1px solid var(--warning-border);background:var(--warning-soft)">'
          + '  <div style="font-size:0.82rem;color:var(--text)">前置内容已完成。检查无误后，可继续全自动写正文 ' + escapeHtml(pendingLabel) + '。</div>'
          + '  <div style="display:flex;gap:0.4rem;flex-wrap:wrap">'
          + '    <button class="ghost tiny" data-ln-confirm-outline>查看大纲</button>'
          + '    <button class="ghost tiny" data-ln-confirm-later>稍后手动写</button>'
          + '    <button class="btn-primary tiny" data-ln-confirm-writing>确认并写正文</button>'
          + '  </div>'
          + '</div>';
        var outlineBtn = confirmEl.querySelector('[data-ln-confirm-outline]');
        var laterBtn = confirmEl.querySelector('[data-ln-confirm-later]');
        var writeBtn = confirmEl.querySelector('[data-ln-confirm-writing]');
        if (outlineBtn) outlineBtn.onclick = function() {
          var tab = document.querySelector('[data-ln-sub="outline"]');
          if (tab) tab.click();
        };
        if (laterBtn) laterBtn.onclick = _lnSkipAutopilotWriting;
        if (writeBtn) writeBtn.onclick = _lnConfirmAutopilotWriting;
      } else {
        confirmEl.style.display = 'none';
        confirmEl.innerHTML = '';
      }
    }
  }

  var _LN_WRITE_STATUS_LABEL = {
    writing: '写作中', drafting: '初稿/扩写/润色/去AI', reviewing: '审核中',
    revising: '保存中', passed: '已成稿', needs_human: '已成稿', error: '出错',
  };

  function _lnRenderAutopilotWriting(writing, state, detail) {
    var el = $('ln-autopilot-writing');
    if (!el) return;
    if (!writing || !writing.total) { el.style.display = 'none'; el.innerHTML = ''; return; }
    el.style.display = '';
    var total = writing.total || 0;
    var done = writing.done || 0;
    var pct = total ? Math.round((done / total) * 100) : 0;
    var cur = writing.current || 0;
    var curStatus = _LN_WRITE_STATUS_LABEL[writing.current_status] || writing.current_status || '';
    var currentDetail = writing.current_detail || {};

    var header = '正文进度：' + done + ' / ' + total + ' 章（' + pct + '%）';
    if (state === 'running' && cur) {
      header += ' · 第' + cur + '章 ' + escapeHtml(curStatus);
    }

    var bar = '<div style="height:6px;border-radius:4px;background:var(--border,#333);overflow:hidden;margin:0.4rem 0">'
      + '<div style="height:100%;width:' + pct + '%;background:var(--primary)"></div></div>';

    var chips = (writing.results || []).map(function(r) {
      var color = 'var(--success)';
      var label = '第' + r.chapter + '章';
      if (r.score) label += ' ' + r.score + '分';
      var title = '已保存成稿；审核分数仅供参考';
      return '<span title="' + escapeHtml(title) + '" style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.72rem;color:' + color + ';border:1px solid ' + color + '">' + escapeHtml(label) + ' · 已成稿</span>';
    }).join('');

    var liveLine = '';
    if (state === 'running') {
      var reason = currentDetail.reason || '';
      var text = reason ? ('审核参考：' + reason) : (detail || '');
      if (text) {
        liveLine = '<div style="font-size:0.78rem;color:var(--muted);line-height:1.6;margin-top:0.15rem">' + escapeHtml(text) + '</div>';
      }
    }

    el.innerHTML = '<div style="font-size:0.82rem;font-weight:600;margin-bottom:0.2rem">' + escapeHtml(header) + '</div>'
      + bar
      + liveLine
      + (chips ? '<div style="display:flex;flex-wrap:wrap;gap:0.35rem;margin-top:0.35rem">' + chips + '</div>' : '');
  }

  // ── 创作准备 ──
  // (setup flow now handled via overview panel's _lnRunSetupPhases / loadBookOverview)


  async function aiSuggestBooks() {
    var statusEl = $('ln-suggest-status');
    var listEl = $('ln-suggestions');
    var btn = $('ln-btn-ai-suggest');
    statusEl.textContent = ' AI分析热门趋势中…';
    btn.disabled = true;
    try {
      var data = await api('/api/long-novel/themes/suggest-books', { method: 'POST', body: {type: 'long', count: 5} });
      var suggestions = data.suggestions || [];
      if (suggestions.length === 0) { statusEl.textContent = ' 暂无推荐，请手动输入'; return; }
      statusEl.textContent = ' 已生成' + suggestions.length + '个选题，点击选择：';
      listEl.style.display = '';
      listEl.innerHTML = suggestions.map(function(s, i) {
        var diffColor = {easy:'var(--success)', medium:'var(--warning)', hard:'var(--danger)'}[s.difficulty] || 'var(--muted)';
        return '<div class="card-glass" style="padding:0.6rem 1rem;margin-bottom:0.4rem;cursor:pointer;border-left:3px solid ' + diffColor + '" data-ln-pick="' + i + '">'
          + '<strong>' + escapeHtml(s.title || '') + '</strong>'
          + ' <span class="badge-indigo">' + escapeHtml(s.genre || '') + '</span>'
          + ' <span class="inbox-meta">' + escapeHtml(s.emotion || '') + '</span>'
          + '<div class="inbox-meta" style="margin-top:0.2rem">' + escapeHtml(s.premise || '') + '</div>'
          + '<div style="font-size:0.7rem;color:var(--muted)">' + escapeHtml(s.trend_reason || '') + ' · ' + escapeHtml(s.target_audience || '') + '</div>'
          + '</div>';
      }).join('');
      // Click to fill
      listEl.querySelectorAll('[data-ln-pick]').forEach(function(card) {
        card.addEventListener('click', function() {
          var s = suggestions[parseInt(card.dataset.lnPick)];
          if (s) {
            $('ln-new-title').value = s.title || '';
            $('ln-new-genre').value = s.genre || '';
            $('ln-new-premise').value = s.premise || '';
            statusEl.textContent = ' ✓ 已选择：' + s.title;
          }
        });
      });
    } catch (err) {
      statusEl.textContent = ' 加载失败：' + err.message;
    } finally { btn.disabled = false; }
  }

  async function deleteBookById(bookId) {
    var bookName = '#' + bookId;
    showConfirm('确定删除「' + bookName + '」？此操作不可撤销，将删除所有章节和设定文件。', async function() {
      try {
        await api('/api/long-novel/books/' + bookId, { method: 'DELETE' });
        toast('已删除', 'success');
        if (_lnActiveBookId === bookId) _lnActiveBookId = null;
        loadBookList();
      } catch (err) { toast('删除失败：' + err.message, 'error'); }
    });
  }

  // ── 写作工作台 ──
  var _lnViewChapter = 0;
  function _lnWorkspaceChapterKey(bookId) {
    return 'anp-ln-view-chapter:' + bookId;
  }
  function _lnPersistWorkspaceState() {
    try {
      if (_lnActiveBookId) {
        localStorage.setItem('anp-ln-active-book', String(_lnActiveBookId));
        if (_lnViewChapter) localStorage.setItem(_lnWorkspaceChapterKey(_lnActiveBookId), String(_lnViewChapter));
        else localStorage.removeItem(_lnWorkspaceChapterKey(_lnActiveBookId));
      } else {
        localStorage.removeItem('anp-ln-active-book');
      }
    } catch (_) {}
  }
  function _lnRestoreWorkspaceChapter(bookId) {
    try {
      var raw = localStorage.getItem(_lnWorkspaceChapterKey(bookId));
      var n = parseInt(raw || '0', 10);
      return Number.isFinite(n) ? n : 0;
    } catch (_) {
      return 0;
    }
  }

  async function loadWritingWorkbench() {
    if (!_lnActiveBookId) {
      $('ln-ws-book-title').textContent = '请先在书库中选择一本书';
      return;
    }
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = data.book || {};
      $('ln-ws-book-title').textContent = '' + escapeHtml(book.title);
      $('ln-ws-progress').textContent = '第' + (book.current_chapter || 0) + '/' + (book.target_chapters || 30) + '章 · ' + escapeHtml(book.genre || '');
      $('ln-ws-progress').textContent = book.title;

      // Load chapters
      if (!_lnViewChapter) _lnViewChapter = _lnRestoreWorkspaceChapter(_lnActiveBookId);
      var chData = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters');
      var chapters = chData.chapters || [];
      var list = $('ln-chapter-list');
      if (chapters.length === 0) {
        list.innerHTML = '<div class="inbox-meta">暂无章节，请先在「全自动生成」里完成前置内容并写正文</div>';
      } else {
        list.innerHTML = '<div style="font-weight:600;margin-bottom:0.3rem">章节列表</div>'
          + chapters.map(function(c) {
              var icon = {outline_only:'', writing:'', draft:'', reviewed:'✓', published:''}[c.status] || '';
              var sel = (c.chapter_number === _lnViewChapter) ? ' style="background:var(--primary-soft)"' : '';
              return '<div class="card-glass" data-ln-ch="' + c.chapter_number + '"' + sel + ' style="display:flex;align-items:center;gap:0.5rem;padding:0.4rem 0.75rem;margin-bottom:0.2rem;cursor:pointer;font-size:0.85rem">'
                + '<span>' + icon + '</span>'
                + '<span style="flex:1">第' + c.chapter_number + '章 ' + escapeHtml(c.title || '') + '</span>'
                + '<span class="inbox-meta" style="font-size:0.7rem">' + (c.actual_words || 0) + '字</span>'
                + '</div>';
            }).join('');
        list.querySelectorAll('[data-ln-ch]').forEach(function(row) {

          row.addEventListener('click', function() {

            hideStepControls();

            _lnViewChapter = parseInt(row.dataset.lnCh);

            loadChapterView(_lnViewChapter);

          });

        });
      }

      // Load next chapter context
      var nextData = await api('/api/long-novel/books/' + _lnActiveBookId + '/next-chapter');
      if (nextData.chapter) {
        _lnViewChapter = nextData.chapter.chapter_number;
        loadChapterView(_lnViewChapter);
      }
    } catch (err) { toast('加载写作台失败：' + err.message, 'error'); }
  }

  function navigateChapter(delta) {

    hideStepControls();

    _lnViewChapter = Math.max(1, _lnViewChapter + delta);

    loadChapterView(_lnViewChapter);

    // Refresh chapter list highlighting

    loadWritingWorkbench();

  }

  async function loadChapterView(chNum) {
    _lnViewChapter = chNum;
    try {
      var chData = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters/' + chNum);
      var ch = chData.chapter || {};
      if (ch.content) {
        $('ln-writing-output').style.display = '';
        $('ln-output-title').textContent = '第' + chNum + '章 ' + escapeHtml(ch.title || '');
        $('ln-output-content').innerHTML = renderMarkdown(ch.content);
      } else {
        $('ln-writing-output').style.display = 'none';
      }
      // Load context
      var ctxData = await api('/api/long-novel/books/' + _lnActiveBookId + '/context/' + chNum);
      var ctx = ctxData.context || {};
      var ctxHtml = '';
      if (ctx.outline) ctxHtml += '<div style="margin-bottom:0.5rem"><strong>章纲：</strong>' + escapeHtml(ctx.outline.substring(0, 500)) + '</div>';
      if (ctx.prev_chapter_summary) ctxHtml += '<div style="margin-bottom:0.5rem"><strong>上章摘要：</strong>' + escapeHtml(ctx.prev_chapter_summary) + '</div>';
      if (ctx.book_progress) ctxHtml += '<div style="margin-bottom:0.5rem"><strong>全书进展：</strong><pre style="font-size:0.75rem;white-space:pre-wrap">' + escapeHtml(ctx.book_progress.substring(0, 1000)) + '</pre></div>';
      if (ctx.continuation_constraints) ctxHtml += '<div style="margin-bottom:0.5rem"><strong>续写约束：</strong><pre style="font-size:0.75rem;white-space:pre-wrap">' + escapeHtml(ctx.continuation_constraints.substring(0, 800)) + '</pre></div>';
      if (ctx.foreshadowing) ctxHtml += '<div style="margin-bottom:0.5rem"><strong>相关伏笔：</strong><pre style="font-size:0.75rem;white-space:pre-wrap">' + escapeHtml(ctx.foreshadowing.substring(0, 1000)) + '</pre></div>';
      if (!ctxHtml) ctxHtml = '<span class="inbox-meta">暂无上下文，请先完成开书设定</span>';
      $('ln-context-content').innerHTML = ctxHtml;
    } catch (err) { /* silent */ }
  }

  async function _lnPhaseHasArtifacts(phaseId) {
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-files?phase=' + phaseId);
      return !!(data.files && data.files.length);
    } catch (_e) {
      return false;
    }
  }

  async function _lnCanFinalizeFromArtifacts() {
    var required = ['premise', 'world', 'characters', 'outline', 'volume_outline', 'chapter_outlines'];
    for (var i = 0; i < required.length; i++) {
      if (!await _lnPhaseHasArtifacts(required[i])) return false;
    }
    return true;
  }

  async function _lnFinalizeSetupFromArtifacts() {
    var btn = $('ln-btn-write-next');
    var release = withBusy(btn, '整理章节队列…');
    try {
      await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/finalize', { method: 'POST', body: {} });
      var started = Date.now();
      while (true) {
        await new Promise(function(r) { setTimeout(r, 1200); });
        var st = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/finalize/status');
        if (st.status === 'done') return true;
        if (st.status === 'error' || st.status === 'cancelled') throw new Error(st.detail || '收尾入库失败');
        if (Date.now() - started > 120000) throw new Error('收尾入库超时，请稍后重试');
      }
    } finally {
      release();
    }
  }

  async function writeNextChapter() {
    if (!_lnActiveBookId) { toast('请先选择一本书', 'error'); return; }
    // Check if book needs setup first
    try {
      var bkData = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = bkData.book || {};
      if (book.status === 'setup') {
        if (await _lnCanFinalizeFromArtifacts()) {
          toast('设定和大纲已完成，正在整理章节队列…', 'info');
          await _lnFinalizeSetupFromArtifacts();
          toast('章节队列已就绪，可以开始写正文', 'success');
          loadBookList();
        } else {
        await _lnStartAutopilot({
          chapterCount: 1,
          maxRevisions: _lnAutopilotMaxRevisions(),
          detail: '启动中：将自动完成开书设定，并在正文前等待确认…',
          successToast: '已启动全自动：完成设定后会停在正文确认',
        });
        return;
        }
      }
      var nextData = await api('/api/long-novel/books/' + _lnActiveBookId + '/next-chapter');

      if (!nextData.chapter) { toast('所有章节已完成。需要继续写时，到「大纲」tab 点「追加章节」。', 'info'); return; }
      await _lnStartAutopilot({
        chapterCount: 1,
        maxRevisions: _lnAutopilotMaxRevisions(),
        pauseBeforeWriting: false,
        detail: '启动中：正在全自动写正文…',
        successToast: '已启动全自动写正文',
      });

    } catch (err) { toast('错误：' + err.message, 'error'); }

  }



  // ── Step-by-step writing ──



  var _lnStepState = {

    active: false,

    chapterNum: 0,

    currentStep: '',

    completedSteps: [],

    chapterTitle: ''

  };



  function showStepControls(chapterNum, chapterTitle) {

    _lnStepState.active = true;

    _lnStepState.chapterNum = chapterNum;

    _lnStepState.chapterTitle = chapterTitle;

    _lnStepState.currentStep = '';

    _lnStepState.completedSteps = [];



    $('ln-step-controls').style.display = '';

    $('ln-step-title').textContent = '第' + chapterNum + '章 — 分步写作';

    $('ln-step-status').textContent = '准备开始';

    $('ln-step-output').style.display = 'none';

    $('ln-step-continuity').style.display = 'none';

    $('ln-step-progress-fill').style.width = '0%';



    // Reset all step buttons

    ['draft','expand','polish','deslop','continuity','finalize'].forEach(function(s) {

      var btn = $('ln-step-btn-' + s);

      if (btn) { btn.disabled = (s !== 'draft'); btn.className = s === 'draft' ? 'btn-primary tiny' : 'btn-primary tiny'; }

    });



    // The full-auto正文 button now lives in the 全自动生成 panel.
    var oneShotBtn = $('ln-btn-write-next');
    if (oneShotBtn) oneShotBtn.style.display = 'none';



    // Check if there are existing step files

    checkStepStatus();

  }



  function hideStepControls() {

    _lnStepState.active = false;

    $('ln-step-controls').style.display = 'none';

    var oneShotBtn = $('ln-btn-write-next');
    if (oneShotBtn) oneShotBtn.style.display = '';

  }



  async function checkStepStatus() {

    try {

      var sd = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + _lnStepState.chapterNum + '/step-status');

      var steps = sd.steps_available || [];

      var stepOrder = ['draft', 'expand', 'polish', 'deslop', 'review'];



      stepOrder.forEach(function(s) {

        var found = steps.find(function(st) { return st.step === s; });

        if (found) {

          _lnStepState.completedSteps.push(s);

          _lnStepState.currentStep = s;

          var btn = $('ln-step-btn-' + s);

          if (btn) { btn.disabled = false; btn.className = 'btn-ghost tiny'; btn.textContent = btn.textContent.replace(/^[12]/, '✓'); }

        }

      });



      updateStepButtons();

    } catch(e) { /* ignore */ }

  }



  async function runStep(stepName) {

    if (!_lnActiveBookId || !_lnStepState.chapterNum) return;



    var stepLabels = {

      draft: '写初稿', expand: '扩写补字', polish: '精修润色',

      deslop: '去AI味', continuity: '连贯检查', finalize: '成稿'

    };



    var btn = $('ln-step-btn-' + stepName);

    var oldText = btn.textContent;

    btn.textContent = '' + stepLabels[stepName] + '中…';

    btn.disabled = true;

    $('ln-step-status').textContent = '正在' + stepLabels[stepName] + '…';



    try {

      var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + _lnStepState.chapterNum + '/step/' + stepName, { method: 'POST' });



      if (stepName === 'continuity') {

        // Show continuity results

        var cdiv = $('ln-step-continuity');

        if (result.skipped) {

          cdiv.style.display = '';

          cdiv.style.background = 'var(--panel-soft)';

          cdiv.innerHTML = '<span class="inbox-meta">' + result.reason + '</span>';

        } else if (result.passed) {

          cdiv.style.display = '';

          cdiv.style.background = 'var(--success-soft)';

          cdiv.innerHTML = '<strong>✓ 连续性检查通过</strong>';

        } else {

          cdiv.style.display = '';

          cdiv.style.background = 'var(--warning-soft)';

          cdiv.innerHTML = '<strong>⚠ 发现 ' + (result.issue_count || 0) + ' 个问题：</strong><br>'

            + (result.issues || []).map(function(i) { return '• ' + escapeHtml(String(i)); }).join('<br>');

        }

        btn.textContent = '✓ 已检查';

        btn.className = 'btn-ghost tiny';

      } else if (stepName === 'finalize') {

        // Finalize done

        var review = result.review || {};

        var verdictEmoji = review.overall === 'APPROVE' ? '✓' : review.overall === 'CONCERNS' ? '⚠' : '✕';

        toast('第' + _lnStepState.chapterNum + '章完成！' + result.final_words + '字 · 审查：' + verdictEmoji + ' ' + (review.overall || '?'), 'success');



        // Show review summary

        $('ln-step-output').style.display = '';

        $('ln-step-output-title').textContent = '四维审查结果：' + verdictEmoji + ' ' + (review.overall || '?');

        var dims = review.dimensions || {};

        var reviewHtml = Object.keys(dims).map(function(k) {

          var d = dims[k];

          var v = d.verdict === 'APPROVE' ? '✓' : d.verdict === 'CONCERNS' ? '⚠' : '✕';

          return '<div style="margin-bottom:0.4rem">' + v + ' <strong>' + k + '</strong>: ' + escapeHtml(String(d.findings ? d.findings[0] : '')).substring(0, 200) + '</div>';

        }).join('');

        $('ln-step-output-content').innerHTML = reviewHtml;



        hideStepControls();

        loadWritingWorkbench();

      } else {

        // Regular writing step

        $('ln-step-output').style.display = '';

        $('ln-step-output-title').textContent = '' + stepLabels[stepName] + ' — ' + (result.word_count || 0) + '字';

        $('ln-step-output-content').innerHTML = '<div style="line-height:1.8">' + renderMarkdown(result.content || '') + '</div>';



        btn.textContent = '✓ ' + stepLabels[stepName];

        btn.className = 'btn-ghost tiny';

        _lnStepState.completedSteps.push(stepName);

        _lnStepState.currentStep = stepName;

        $('ln-step-status').textContent = stepLabels[stepName] + '完成 · ' + (result.word_count || 0) + '字';

      }



      updateStepButtons();

    } catch (err) {

      toast(stepLabels[stepName] + '失败：' + err.message, 'error');

      btn.textContent = oldText;

      btn.disabled = false;

      $('ln-step-status').textContent = '失败';

    }

  }



  function updateStepButtons() {

    var progress = 0;

    var allSteps = ['draft', 'expand', 'polish', 'deslop', 'continuity', 'finalize'];

    var completed = _lnStepState.completedSteps;



    allSteps.forEach(function(s, i) {

      var btn = $('ln-step-btn-' + s);

      if (!btn) return;



      if (completed.indexOf(s) >= 0) {

        // Already done

        btn.disabled = false;

        if (btn.className.indexOf('btn-ghost') < 0) {

          btn.className = 'btn-ghost tiny';

          var label = btn.textContent.replace(/^[✓✕12]/,'');

          btn.textContent = '✓ ' + label;

        }

        progress = i + 1;

      } else if (i === 0 || completed.indexOf(allSteps[i-1]) >= 0) {

        // This step is available now

        btn.disabled = false;

        btn.className = 'btn-primary tiny';

        progress = Math.max(progress, i);

      } else if (s === 'finalize' && completed.length >= 2) {

        // Allow finalize even if continuity was skipped

        btn.disabled = false;

        btn.className = 'btn-success tiny';

      } else {

        btn.disabled = true;

      }

    });



    $('ln-step-progress-fill').style.width = Math.round(progress / allSteps.length * 100) + '%';

  }



  var _lnWritingStepOrder = ['draft', 'expand', 'polish', 'deslop', 'review', 'finalize'];
  var _lnWritingSteps = [
    {id: 'draft',    icon: '', label: '写初稿', desc: '基于章纲、设定、上下文生成初稿。下方展开「LLM 输入材料」可看到本次喂给模型的内容。'},
    {id: 'expand',   icon: '', label: '扩写',   desc: '初稿未满3000字时调用；达到3000字会自动跳过，也可强制扩写。'},
    {id: 'polish',   icon: '', label: '润色',   desc: '精修语病、节奏、对话与画面感，不改情节。'},
    {id: 'deslop',   icon: '', label: '去 AI',  desc: '清除 AI 高频词与套路句式。运行后下方显示前后对比，可一键复制全文。'},
    {id: 'review',   icon: '', label: '审查',   desc: '检查去 AI 后的最终候选稿：连续性 / 逻辑 / 剧情推进 / 人设 / 环境 / 共情。'},
    {id: 'finalize', icon: '✓', label: '成稿',   desc: '正文存档、更新追踪记忆与续写约束。'}
  ];
  var _lnWritingStepLabels = _lnWritingSteps.reduce(function(m, s) { m[s.id] = s.label; return m; }, {});
  var _lnRunningStepPollers = {};

  function _lnStepPollKey(chNum, stepName) {
    return String(_lnActiveBookId || '') + ':' + String(chNum || '') + ':' + String(stepName || '');
  }

  function _lnStopChapterStepPolling(chNum, stepName) {
    var key = _lnStepPollKey(chNum, stepName);
    if (_lnRunningStepPollers[key]) {
      clearInterval(_lnRunningStepPollers[key]);
      delete _lnRunningStepPollers[key];
    }
  }

  function _lnStopAllChapterStepPollers() {
    Object.keys(_lnRunningStepPollers).forEach(function(key) {
      clearInterval(_lnRunningStepPollers[key]);
      delete _lnRunningStepPollers[key];
    });
  }

  async function _lnHandleChapterStepCompletion(chNum, stepName, statusData) {
    var row = _lnFindStepRow(stepName);
    var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName);
    if (row) {
      _lnRenderSavedStepOutput(row, stepName, result || {});
    }
    _lnSetStepStatus(stepName, result && result.skipped ? 'skipped' : 'done', result && result.skipped ? '已自动跳过' : null);
    var idx = _lnWritingStepOrder.indexOf(stepName);
    for (var i = 0; i < idx; i++) {
      var prevRow = _lnFindStepRow(_lnWritingStepOrder[i]);
      if (
        prevRow
        && !prevRow.classList.contains('status-done')
        && !prevRow.classList.contains('status-skipped')
      ) {
        _lnSetStepStatus(_lnWritingStepOrder[i], 'done');
      }
    }
    if (statusData && statusData.detail) {
      toast(statusData.detail, result && result.skipped ? 'info' : 'success');
    }
    if (stepName === 'finalize') {
      await loadWritingWorkbench();
    }
  }

  async function _lnPollChapterStep(chNum, stepName, silent) {
    if (!_lnActiveBookId || !chNum) return;
    var data = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName + '/status');
    var status = String(data.status || 'pending');
    if (status === 'starting' || status === 'running') {
      _lnSetStepStatus(stepName, 'running');
      return;
    }
    _lnStopChapterStepPolling(chNum, stepName);
    if (status === 'done' || status === 'skipped') {
      await _lnHandleChapterStepCompletion(chNum, stepName, data);
      return;
    }
    if (status === 'error' || status === 'cancelled') {
      _lnSetStepStatus(stepName, 'error', '✕ ' + String(data.detail || '失败').slice(0, 40));
      if (!silent) toast((_lnWritingStepLabels[stepName] || stepName) + '失败：' + (data.detail || '任务中断'), 'error');
    }
  }

  function _lnStartChapterStepPolling(chNum, stepName, options) {
    if (!_lnActiveBookId || !chNum) return;
    options = options || {};
    var key = _lnStepPollKey(chNum, stepName);
    if (_lnRunningStepPollers[key]) return;
    _lnRunningStepPollers[key] = setInterval(function() {
      _lnPollChapterStep(chNum, stepName, true).catch(function() {});
    }, options.interval || 2000);
    _lnPollChapterStep(chNum, stepName, !!options.silent).catch(function() {});
  }

  function ensureWritingPanelLayout() {
    var panel = document.getElementById('ln-writing-panel');
    if (!panel || panel.dataset.layoutReady === '1') return;
    panel.dataset.layoutReady = '1';
    panel.innerHTML = ''
      + '<div class="ln-writing-topbar">'
      + '  <div><h4>正文工作台</h4><p>章节数量跟随章节细纲；点开任意章节卡片可手动分步。全自动正文范围在「全自动生成」里选择。</p></div>'
      + '  <div class="ln-writing-actions">'
      + '    <button class="btn-warning tiny" id="ln-btn-rewrite">重写当前章</button>'
      + '    <button class="ghost tiny" id="ln-btn-prev-chapter">◀ 上一章</button>'
      + '    <button class="ghost tiny" id="ln-btn-next-chapter">下一章 ▶</button>'
      + '  </div>'
      + '</div>'
      + '<div id="ln-chapter-list" class="ln-chapter-card-grid"></div>';

    // Bind action buttons (they didn't exist at bindLongNovel time)
    var bRewrite   = document.getElementById('ln-btn-rewrite');
    var bPrev      = document.getElementById('ln-btn-prev-chapter');
    var bNext      = document.getElementById('ln-btn-next-chapter');
    if (bRewrite)   bRewrite.addEventListener('click', function() {
      if (typeof rewriteCurrentChapter === 'function') rewriteCurrentChapter();
    });
    if (bPrev) bPrev.addEventListener('click', function() { navigateChapter(-1); });
    if (bNext) bNext.addEventListener('click', function() { navigateChapter(1); });
  }

  function _lnContextItemHtml(label, value, required) {
    var text = String(value || '').trim();
    var state = text ? 'ready' : (required ? 'missing' : 'optional');
    var badge = text ? '已读取' : (required ? '缺失' : '无');
    var count = text ? text.length + '字' : '0字';
    return '<div class="ln-step-material ' + state + '">'
      + '<strong>' + escapeHtml(label) + '</strong>'
      + '<span>' + badge + ' · ' + count + '</span>'
      + '</div>';
  }

  function _lnDraftManifestHtml(ctx) {
    var items = [
      ['本章细纲', ctx.outline, true],
      ['全书大纲', ctx.book_outline, true],
      ['卷纲', ctx.volume_outline, true],
      ['上章结尾/摘要', ctx.prev_chapter_last_paras || ctx.prev_chapter_summary, false],
      ['全书进展', ctx.book_progress, false],
      ['续写约束', ctx.continuation_constraints, true],
      ['角色状态', ctx.character_states, false],
      ['角色设定', ctx.character_profiles, true],
      ['人物关系', ctx.relationships, true],
      ['世界观', ctx.world, true],
      ['伏笔', ctx.foreshadowing, false],
      ['时间线', ctx.timeline, false],
      ['题材定位', ctx.premise, true]
    ];
    return '<div class="ln-step-material-grid" style="padding:0">'
      + items.map(function(item) { return _lnContextItemHtml(item[0], item[1], item[2]); }).join('')
      + '</div>';
  }

  function _lnRenderReviewResult(review) {
    if (!review) return '<div class="empty">无审查数据</div>';
    var dims = review.dimensions || review.details || {};
    var names = {
      continuity:          ['', '连续性检查',     '是否承接前文与长期记忆'],
      logic:               ['', '逻辑',           '因果是否成立、是否自洽'],
      plot_progress:       ['', '剧情是否推进',   '本章是否真的把主线推进了'],
      character_integrity: ['', '人设是否崩塌',   '语言、动机、关系是否沿用设定'],
      environment:         ['', '环境是否恰当',   '世界观、场景、时代是否冲突'],
      empathy:             ['', '读者是否能共情', '情绪铺垫、爽点、痛点是否成立']
    };
    var overall = String(review.overall || 'CONCERNS').toUpperCase();
    var overallCls = overall === 'APPROVE' ? 'approve' : (overall === 'REJECT' ? 'reject' : 'concerns');
    var head = '<div style="margin-bottom:0.5rem"><strong>总评：</strong><span class="ln-review-dim-verdict ' + overallCls + '">' + escapeHtml(overall) + '</span></div>';
    var body = '<div class="ln-review-dim-grid">' + Object.keys(names).map(function(k) {
      var info = names[k];
      var d = dims[k] || {};
      return _lnRenderReviewDimension(info, d);
    }).join('') + '</div>';
    var topRecs = _lnRenderList(review.recommendations || []);
    return head + body + (topRecs ? '<div class="ln-review-top-recs"><strong>总体建议</strong><ul>' + topRecs + '</ul></div>' : '');
  }

  function _lnChapterStepStatus(ch, available, skipped, progressRows) {
    // Returns map: stepId -> 'done' | 'next' | 'pending' | 'skipped'
    // available comes from /step-status: steps that have intermediate files in work_dir
    var map = {};
    var done = new Set(available || []);
    var skip = new Set(skipped || []);
    var progressMap = {};
    (progressRows || []).forEach(function(item) {
      if (item && item.step) progressMap[item.step] = item;
    });
    if (ch && (ch.status === 'draft' || ch.status === 'published')) {
      // Even for finished chapters, respect skipped status
      _lnWritingStepOrder.forEach(function(s) {
        map[s] = skip.has(s) ? 'skipped' : 'done';
      });
      return map;
    }
    var foundFirst = false;
    _lnWritingStepOrder.forEach(function(s) {
      var p = progressMap[s] || {};
      var ps = String(p.status || '');
      if (ps === 'starting' || ps === 'running') {
        map[s] = 'running';
        foundFirst = true;
      } else if (ps === 'error' || ps === 'cancelled') {
        map[s] = 'error';
        foundFirst = true;
      } else if (skip.has(s) || ps === 'skipped') {
        map[s] = 'skipped';
      } else if (done.has(s) || ps === 'done') {
        map[s] = 'done';
      } else if (!foundFirst) {
        map[s] = 'next';
        foundFirst = true;
      } else {
        map[s] = 'pending';
      }
    });
    return map;
  }

  function _lnChapterCardHtml(c) {
    var active = c.chapter_number === _lnViewChapter ? ' active' : '';
    var done = (c.status === 'draft' || c.status === 'published');
    var statusText = done ? '已成稿' : (c.status === 'writing' ? '写作中' : '待写');
    var words = c.actual_words || 0;
    var review = c.review_status ? '<span class="ln-chapter-review">' + escapeHtml(c.review_status) + '</span>' : '';
    var progressDots = _lnWritingStepOrder.map(function(_s, i) {
      var cls = done ? 'done' : (c.status === 'writing' && i === 0 ? 'running' : '');
      return '<span class="' + cls + '"></span>';
    }).join('');
    return '<button class="ln-chapter-card' + active + '" data-ln-ch="' + c.chapter_number + '">'
      + '<span class="ln-chapter-card-top"><strong>第' + c.chapter_number + '章</strong><em>' + statusText + '</em></span>'
      + '<span class="ln-chapter-card-title">' + escapeHtml(c.title || '未命名章节') + '</span>'
      + '<span class="ln-chapter-card-meta">' + words + '字 ' + review + '</span>'
      + '<span class="ln-chapter-card-progress">' + progressDots + '</span>'
      + '</button>';
  }

  function _lnStepRowHtml(step, status) {
    var stCls = status === 'done' ? ' status-done' : (status === 'running' ? ' status-running' : (status === 'error' ? ' status-error' : ''));
    var stLabel = status === 'done' ? '✓ 已完成' : (status === 'running' ? '进行中' : (status === 'error' ? '✕ 失败' : '待办'));
    var idx = _lnWritingStepOrder.indexOf(step.id);
    var btnClass = step.id === 'finalize' ? 'btn-success tiny' : 'btn-primary tiny';
    var btnLabel = status === 'done' ? '重做' : (idx === 0 ? '▶ 运行' : '▶ 运行');
    return '<div class="ln-step-row' + stCls + '" data-step-row="' + step.id + '">'
      + '  <div class="ln-step-row-head">'
      + '    <div class="ln-step-row-head-main">'
      + '      <span class="ln-step-row-num">' + (idx + 1) + '</span>'
      + '      <span class="ln-step-row-icon">' + step.icon + '</span>'
      + '      <span class="ln-step-row-name">' + escapeHtml(step.label) + '</span>'
      + '      <span class="ln-step-row-desc">' + escapeHtml(step.desc) + '</span>'
      + '    </div>'
      + '    <div class="ln-step-row-head-side">'
      + '      <span class="ln-step-row-status" data-step-status>' + stLabel + '</span>'
      + '      <span class="ln-step-row-actions">'
      + '        <button class="' + btnClass + '" data-step-run>' + btnLabel + '</button>'
      + '      </span>'
      + '    </div>'
      + '  </div>'
      + '  <div class="ln-step-row-body" data-step-body>'
      + _lnStepRowBodyHtml(step)
      + '  </div>'
      + '</div>';
  }

  function _lnStepRowBodyHtml(step) {
    if (step.id === 'draft') {
      // manifest 面板已移到「链路」tab，写作流程不再加载（避免卡 WebUI）
      return ''
        + '<details data-step-section="output"><summary>初稿预览</summary>'
        + '  <div class="ln-step-output-preview" data-step-output><div class="empty" style="padding:0.5rem">运行后显示初稿内容</div></div>'
        + '</details>';
    }
    if (step.id === 'review') {
      return ''
        + '<details data-step-section="output"><summary>六维审查结果</summary>'
        + '  <div data-step-output><div class="empty" style="padding:0.5rem">运行后显示：连续性 / 逻辑 / 剧情推进 / 人设 / 环境 / 共情</div></div>'
        + '</details>';
    }
    if (step.id === 'deslop') {
      return ''
        + '<div class="ln-deslop-toolbar">'
        + '  <button class="ghost tiny" data-step-copy-all>复制全文</button>'
        + '  <span class="inbox-meta" style="font-size:0.75rem">如需第三方 AI 味检测，可复制后到外部网页粘贴</span>'
        + '</div>'
        + '<details data-step-section="diff"><summary>原文 / 去 AI 后 对比</summary>'
        + '  <div data-step-diff><div class="empty" style="padding:0.5rem">运行后显示对比</div></div>'
        + '</details>'
        + '<div data-step-output style="display:none"><div class="empty"></div></div>'
        + '<details data-step-section="history"><summary>历史版本（每次重做都会归档）</summary>'
        + '  <div data-step-history><div class="empty" style="padding:0.5rem">点击展开后加载</div></div>'
        + '</details>';
    }
    if (step.id === 'finalize') {
      return ''
        + '<details data-step-section="output"><summary>✓ 成稿结果</summary>'
        + '  <div data-step-output><div class="empty" style="padding:0.5rem">运行后：正文存档到「正文/」，并更新追踪长期记忆</div></div>'
        + '</details>';
    }
    // expand / polish 等步骤额外提供对比视图
    if (step.id === 'expand' || step.id === 'polish') {
      var diffLabel = step.id === 'expand' ? '初稿 / 扩写后 对比' : '上一步 / 润色后 对比';
      return ''
        + '<details data-step-section="diff"><summary>' + diffLabel + '</summary>'
        + '  <div data-step-diff><div class="empty" style="padding:0.5rem">运行后显示对比</div></div>'
        + '</details>'
        + '<details data-step-section="output"><summary>' + escapeHtml(step.label) + '预览</summary>'
        + '  <div class="ln-step-output-preview" data-step-output><div class="empty" style="padding:0.5rem">运行后显示内容</div></div>'
        + '</details>'
        + '<details data-step-section="history"><summary>历史版本（每次重做都会归档）</summary>'
        + '  <div data-step-history><div class="empty" style="padding:0.5rem">点击展开后加载</div></div>'
        + '</details>';
    }
    if (step.id === 'draft') {
      return ''
        + '<details data-step-section="output"><summary>初稿预览</summary>'
        + '  <div class="ln-step-output-preview" data-step-output><div class="empty" style="padding:0.5rem">运行后显示初稿内容</div></div>'
        + '</details>'
        + '<details data-step-section="history"><summary>历史版本（每次重做都会归档）</summary>'
        + '  <div data-step-history><div class="empty" style="padding:0.5rem">点击展开后加载</div></div>'
        + '</details>';
    }
    return ''
      + '<details data-step-section="output"><summary>' + escapeHtml(step.label) + '预览</summary>'
      + '  <div class="ln-step-output-preview" data-step-output><div class="empty" style="padding:0.5rem">运行后显示内容</div></div>'
      + '</details>';
  }

  function _lnFindStepRow(stepId) {
    var expand = document.querySelector('.ln-chapter-expand[data-ln-ch-expand]');
    if (!expand) return null;
    return expand.querySelector('[data-step-row="' + stepId + '"]');
  }

  _lnStepRowHtml = function(step, status) {
    var stCls = status === 'done' ? ' status-done' : (status === 'skipped' ? ' status-skipped' : (status === 'running' ? ' status-running' : (status === 'error' ? ' status-error' : '')));
    var stLabel = status === 'done' ? '✓ 已完成' : (status === 'skipped' ? '已跳过' : (status === 'running' ? '进行中' : (status === 'error' ? '✕ 失败' : 'Ⅱ 待办')));
    var idx = _lnWritingStepOrder.indexOf(step.id);
    var btnClass = step.id === 'finalize' ? 'btn-success tiny' : 'btn-primary tiny';
    var btnLabel = status === 'done' ? '重做' : '▶ 运行';
    var skipBtn = step.id === 'finalize' ? '' : '<button class="ghost tiny" data-step-skip>跳过</button>';
    var forceExpandBtn = step.id === 'expand' ? '<button class="btn-warning tiny" data-step-force-expand>强制扩写</button>' : '';
    var promptBtn = ['draft', 'expand', 'polish', 'deslop', 'review', 'finalize', 'continuity'].indexOf(step.id) >= 0 ? '<button class="ghost tiny" data-step-prompt>提示词</button>' : '';
    return '<div class="ln-step-row' + stCls + '" data-step-row="' + step.id + '">'
      + '  <div class="ln-step-row-head">'
      + '    <div class="ln-step-row-head-main">'
      + '      <span class="ln-step-row-num">' + (idx + 1) + '</span>'
      + '      <span class="ln-step-row-icon">' + step.icon + '</span>'
      + '      <span class="ln-step-row-name">' + escapeHtml(step.label) + '</span>'
      + '      <span class="ln-step-row-desc">' + escapeHtml(step.desc) + '</span>'
      + '    </div>'
      + '    <div class="ln-step-row-head-side">'
      + '      <span class="ln-step-row-status" data-step-status>' + stLabel + '</span>'
      + '      <span class="ln-step-row-actions">'
      + promptBtn
      + skipBtn
      + forceExpandBtn
      + '        <button class="' + btnClass + '" data-step-run>' + btnLabel + '</button>'
      + '      </span>'
      + '    </div>'
      + '  </div>'
      + '  <div class="ln-step-row-body" data-step-body>'
      + _lnStepRowBodyHtml(step)
      + '  </div>'
      + '</div>';
  };

  function _lnSetStepStatus(stepId, status, labelOverride) {
    var row = _lnFindStepRow(stepId);
    if (!row) return;
    row.classList.remove('status-done', 'status-running', 'status-error', 'status-skipped');
    if (status === 'done') row.classList.add('status-done');
    else if (status === 'running') row.classList.add('status-running');
    else if (status === 'error') row.classList.add('status-error');
    else if (status === 'skipped') row.classList.add('status-skipped');
    var st = row.querySelector('[data-step-status]');
    if (st) {
      st.textContent = labelOverride || (status === 'done' ? '✓ 已完成'
        : status === 'skipped' ? '⊘ 已跳过'
        : status === 'running' ? '进行中'
        : status === 'error' ? '✕ 失败'
        : 'Ⅱ 待办');
    }
    var runBtn = row.querySelector('[data-step-run]');
    if (runBtn) {
      runBtn.disabled = status === 'running';
      runBtn.textContent = status === 'done' ? '重做' : '▶ 运行';
    }
  }

  function _lnSetStepBodyExpanded(stepId, open) {
    var row = _lnFindStepRow(stepId);
    if (!row) return;
    if (open) row.classList.add('expanded');
    else row.classList.remove('expanded');
  }

  _lnSetStepStatus = function(stepId, status, labelOverride) {
    var row = _lnFindStepRow(stepId);
    if (!row) return;
    row.classList.remove('status-done', 'status-skipped', 'status-running', 'status-error');
    if (status === 'done') row.classList.add('status-done');
    else if (status === 'skipped') row.classList.add('status-skipped');
    else if (status === 'running') row.classList.add('status-running');
    else if (status === 'error') row.classList.add('status-error');
    var st = row.querySelector('[data-step-status]');
    if (st) {
      st.textContent = labelOverride || (status === 'done' ? '✓ 已完成'
        : status === 'skipped' ? '已跳过'
        : status === 'running' ? '进行中'
        : status === 'error' ? '✕ 失败'
        : 'Ⅱ 待办');
    }
    var runBtn = row.querySelector('[data-step-run]');
    if (runBtn) {
      runBtn.disabled = status === 'running';
      runBtn.textContent = status === 'done' ? '重做' : '▶ 运行';
    }
  };

  _lnSetStepBodyExpanded = function(stepId, open) {
    var row = _lnFindStepRow(stepId);
    if (!row) return;
    if (open) row.classList.add('expanded');
    else row.classList.remove('expanded');
    var toggleBtn = row.querySelector('[data-step-toggle]');
    if (toggleBtn) toggleBtn.textContent = open ? '收起' : '展开';
  };

  _lnSetStepBodyExpanded = function(stepId, open) {
    var row = _lnFindStepRow(stepId);
    if (!row) return;
    if (open) {
      var wrap = row.closest('.ln-step-rows');
      if (wrap) {
        wrap.querySelectorAll('.ln-step-row.expanded').forEach(function(other) {
          if (other !== row) {
            other.classList.remove('expanded');
            var otherBtn = other.querySelector('[data-step-toggle]');
            if (otherBtn) otherBtn.textContent = '展开';
          }
        });
      }
      row.classList.add('expanded');
    } else {
      row.classList.remove('expanded');
    }
    var toggleBtn = row.querySelector('[data-step-toggle]');
    if (toggleBtn) toggleBtn.textContent = open ? '收起' : '展开';
  };

  async function _lnLoadDraftManifestInto(chNum) {
    var row = _lnFindStepRow('draft');
    if (!row) return;
    var holder = row.querySelector('[data-step-manifest]');
    if (!holder) return;
    holder.innerHTML = '<div class="empty" style="padding:0.5rem">加载中…</div>';
    try {
      var ctxData = await api('/api/long-novel/books/' + _lnActiveBookId + '/context/' + chNum);
      holder.innerHTML = _lnDraftManifestHtml(ctxData.context || {});
    } catch (_e) {
      holder.innerHTML = '<div class="empty" style="padding:0.5rem">加载上下文失败</div>';
    }
  }

  function _lnDiffOps(a, b) {
    // 段落级 LCS diff，返回 [{op:'eq'|'del'|'add', value: ''}, ...]
    var m = a.length, n = b.length;
    if (!m && !n) return [];
    if (!m) return b.map(function(p) { return {op: 'add', value: p}; });
    if (!n) return a.map(function(p) { return {op: 'del', value: p}; });
    var dp = [];
    for (var i = 0; i <= m; i++) {
      dp.push(new Array(n + 1).fill(0));
    }
    for (var i = 1; i <= m; i++) {
      for (var j = 1; j <= n; j++) {
        if (a[i - 1] === b[j - 1]) dp[i][j] = dp[i - 1][j - 1] + 1;
        else dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
    var ops = [];
    var ai = m, bj = n;
    while (ai > 0 && bj > 0) {
      if (a[ai - 1] === b[bj - 1]) {
        ops.unshift({op: 'eq', value: a[ai - 1]});
        ai--; bj--;
      } else if (dp[ai - 1][bj] >= dp[ai][bj - 1]) {
        ops.unshift({op: 'del', value: a[ai - 1]});
        ai--;
      } else {
        ops.unshift({op: 'add', value: b[bj - 1]});
        bj--;
      }
    }
    while (ai > 0) { ops.unshift({op: 'del', value: a[--ai]}); }
    while (bj > 0) { ops.unshift({op: 'add', value: b[--bj]}); }
    return ops;
  }

  function _lnDiffLabels(stepName) {
    if (stepName === 'expand') {
      return {before: '初稿', after: '扩写后', beforeEmpty: '初稿为空', afterEmpty: '扩写后为空'};
    }
    if (stepName === 'polish') {
      return {before: '上一步', after: '润色后', beforeEmpty: '上一步为空', afterEmpty: '润色后为空'};
    }
    if (stepName === 'deslop') {
      return {before: '原文', after: '去 AI 后', beforeEmpty: '原文为空', afterEmpty: '去 AI 后为空'};
    }
    return {before: '原文', after: '修改后', beforeEmpty: '原文为空', afterEmpty: '修改后为空'};
  }

  function _lnDiffParagraphHtml(text, opts) {
    opts = opts || {};
    var classes = ['ln-diff-paragraph'];
    var attrs = '';
    var marker = '';
    if (opts.changeIdx) {
      classes.push('ln-diff-change');
      classes.push(opts.kind === 'del' ? 'ln-diff-change-del' : 'ln-diff-change-add');
      attrs += ' data-change-idx="' + Number(opts.changeIdx) + '"';
      marker = '<button type="button" class="ln-diff-change-index" data-change-idx="' + Number(opts.changeIdx) + '" title="定位到第 ' + Number(opts.changeIdx) + ' 处修改">' + Number(opts.changeIdx) + '</button>';
    }
    return '<p class="' + classes.join(' ') + '"' + attrs + '>' + marker + '<span class="ln-diff-paragraph-text">' + escapeHtml(text) + '</span></p>';
  }

  function _lnSetDiffActiveChange(view, nextIndex, opts) {
    if (!view) return;
    var total = Number(view.dataset.diffCount || 0);
    if (!total) return;
    opts = opts || {};
    var clamped = Math.max(1, Math.min(total, Number(nextIndex || 1)));
    view.dataset.diffActive = String(clamped);
    view.querySelectorAll('.ln-diff-change.is-active, .ln-diff-change-index.is-active').forEach(function(el) {
      el.classList.remove('is-active');
    });
    var targets = view.querySelectorAll('[data-change-idx="' + clamped + '"]');
    targets.forEach(function(el) {
      el.classList.add('is-active');
    });
    var counter = view.querySelector('[data-diff-counter]');
    if (counter) counter.textContent = clamped + ' / ' + total;
    var prevBtn = view.querySelector('[data-diff-nav="prev"]');
    var nextBtn = view.querySelector('[data-diff-nav="next"]');
    if (prevBtn) prevBtn.disabled = total <= 1;
    if (nextBtn) nextBtn.disabled = total <= 1;
    if (opts.scroll === false) return;
    view.querySelectorAll('.ln-diff-pane-body').forEach(function(pane) {
      var target = pane.querySelector('.ln-diff-change[data-change-idx="' + clamped + '"]');
      if (target) target.scrollIntoView({behavior: opts.behavior || 'smooth', block: 'center'});
    });
  }

  function _lnBindDiffNavigator(diffHost) {
    if (!diffHost || diffHost.dataset.diffBound === '1') return;
    diffHost.dataset.diffBound = '1';
    diffHost.addEventListener('click', function(e) {
      var view = diffHost.querySelector('[data-diff-view]');
      if (!view) return;
      var navBtn = e.target.closest('[data-diff-nav]');
      if (navBtn) {
        e.preventDefault();
        var total = Number(view.dataset.diffCount || 0);
        if (!total) return;
        var current = Number(view.dataset.diffActive || 1);
        var delta = navBtn.dataset.diffNav === 'prev' ? -1 : 1;
        var next = current + delta;
        if (next < 1) next = total;
        if (next > total) next = 1;
        _lnSetDiffActiveChange(view, next, {scroll: true});
        return;
      }
      var badge = e.target.closest('[data-change-idx]');
      if (badge && view.contains(badge)) {
        e.preventDefault();
        _lnSetDiffActiveChange(view, Number(badge.dataset.changeIdx || 1), {scroll: true});
      }
    });
  }

  function _lnRenderDiffView(before, after, labels) {
    before = String(before || '');
    after = String(after || '');
    labels = labels || _lnDiffLabels('');
    if (!before && !after) {
      return '<div class="empty" style="padding:0.5rem">没有内容可对比</div>';
    }
    var beforeWords = (before.match(/[一-龥]/g) || []).length;
    var afterWords = (after.match(/[一-龥]/g) || []).length;
    var diff = afterWords - beforeWords;
    var deltaText = (diff >= 0 ? '+' : '') + diff + '字';
    var paraBefore = before.split(/\n+/).map(function(s) { return s.trim(); }).filter(function(s) { return s.length > 0; });
    var paraAfter  = after.split(/\n+/).map(function(s) { return s.trim(); }).filter(function(s) { return s.length > 0; });
    var ops = _lnDiffOps(paraBefore, paraAfter);
    var addCount = 0, delCount = 0;
    ops.forEach(function(o) { if (o.op === 'add') addCount++; else if (o.op === 'del') delCount++; });
    var originalParts = [];
    var revisedParts = [];
    var opIndex = 0;
    var changeCount = 0;
    while (opIndex < ops.length) {
      var op = ops[opIndex];
      if (op.op === 'eq') {
        originalParts.push(_lnDiffParagraphHtml(op.value));
        revisedParts.push(_lnDiffParagraphHtml(op.value));
        opIndex += 1;
        continue;
      }
      changeCount += 1;
      while (opIndex < ops.length && ops[opIndex].op !== 'eq') {
        var chunk = ops[opIndex];
        if (chunk.op === 'del') originalParts.push(_lnDiffParagraphHtml(chunk.value, {changeIdx: changeCount, kind: 'del'}));
        if (chunk.op === 'add') revisedParts.push(_lnDiffParagraphHtml(chunk.value, {changeIdx: changeCount, kind: 'add'}));
        opIndex += 1;
      }
    }
    var originalHtml = originalParts.join('');
    var revisedHtml = revisedParts.join('');
    var navHtml = '<div class="ln-diff-nav">'
      + '  <div class="ln-diff-nav-main">'
      + '    <button type="button" class="ghost tiny" data-diff-nav="prev"' + (changeCount ? '' : ' disabled') + '>上一处</button>'
      + '    <button type="button" class="ghost tiny" data-diff-nav="next"' + (changeCount ? '' : ' disabled') + '>下一处</button>'
      + '    <span class="ln-diff-nav-counter" data-diff-counter>' + (changeCount ? ('1 / ' + changeCount) : '无改动') + '</span>'
      + '  </div>'
      + '  <div class="ln-diff-nav-tip">' + (changeCount ? ('共 ' + changeCount + ' 处修改，可点击编号直接跳转') : '当前没有检测到段落级修改') + '</div>'
      + '</div>';
    return '<div class="ln-diff-view" data-diff-view data-diff-count="' + changeCount + '" data-diff-active="' + (changeCount ? 1 : 0) + '">'
      + navHtml
      + '<div class="ln-diff-meta inbox-meta">' + escapeHtml(labels.before) + ' ' + beforeWords + '字 → ' + escapeHtml(labels.after) + ' ' + afterWords + '字（' + deltaText + '）'
      + ' · <span class="ln-diff-stat-add">+' + addCount + '段</span>'
      + ' · <span class="ln-diff-stat-del">-' + delCount + '段</span></div>'
      + '<div class="ln-diff-split">'
      + '  <section class="ln-diff-pane ln-diff-pane-before">'
      + '    <header><strong>' + escapeHtml(labels.before) + '</strong><span>' + beforeWords + '字 · ' + paraBefore.length + '段</span></header>'
      + '    <div class="ln-diff-pane-body">' + (originalHtml || '<div class="empty" style="padding:0.5rem">' + escapeHtml(labels.beforeEmpty) + '</div>') + '</div>'
      + '  </section>'
      + '  <section class="ln-diff-pane ln-diff-pane-after">'
      + '    <header><strong>' + escapeHtml(labels.after) + '</strong><span>' + afterWords + '字 · ' + paraAfter.length + '段</span></header>'
      + '    <div class="ln-diff-pane-body">' + (revisedHtml || '<div class="empty" style="padding:0.5rem">' + escapeHtml(labels.afterEmpty) + '</div>') + '</div>'
      + '  </section>'
      + '</div>'
      + '</div>';
  }

  function _lnPopulateDiffSection(row, result) {
    if (!row) return;
    var diffEl = row.querySelector('[data-step-diff]');
    if (!diffEl) return;
    var stepName = (result && result.step) || row.dataset.stepRow || '';
    diffEl.innerHTML = _lnRenderDiffView(result.source_before, result.content, _lnDiffLabels(stepName));
    _lnBindDiffNavigator(diffEl);
    _lnSetDiffActiveChange(diffEl.querySelector('[data-diff-view]'), 1, {scroll: false});
  }

  function _lnRenderSavedStepOutput(row, stepName, result) {
    var outEl = row ? row.querySelector('[data-step-output]') : null;
    if (!outEl) return;
    var editableSteps = ['draft', 'expand', 'polish', 'deslop'];
    var isEditable = editableSteps.indexOf(stepName) >= 0 && result.content;
    var editBtn = isEditable ? ' <button class="ln-step-edit-btn" data-step-edit="' + stepName + '">编辑</button>' : '';

    if (stepName === 'review') {
      outEl.innerHTML = _lnRenderReviewResult(result.review || {}, result.force_pass || {});
    } else if (stepName === 'deslop') {
      outEl.innerHTML = '';
    } else if (stepName === 'finalize') {
      outEl.innerHTML = '<div style="margin-bottom:0.5rem"><strong>✓ 已成稿 · ' + (result.final_words || result.word_count || 0) + '字</strong>'
        + '<br><span class="inbox-meta">' + escapeHtml(result.draft_path || '') + '</span></div>'
        + (result.content ? '<div class="ln-step-output-preview">' + renderMarkdown(result.content || '') + '</div>' : '');
    } else if (result.skipped && stepName === 'expand') {
      outEl.innerHTML = '<div style="margin-bottom:0.4rem"><strong>已自动跳过扩写 · 初稿 ' + (result.word_count || 0) + '字</strong>'
        + '<br><span class="inbox-meta">' + escapeHtml(result.message || '初稿已达到 3000 字，无需扩写。') + '</span></div>'
        + '<div class="ln-step-output-preview">' + renderMarkdown(result.content || '') + '</div>';
    } else if (stepName === 'draft') {
      outEl.innerHTML = '<div style="margin-bottom:0.4rem"><strong>初稿 · ' + (result.word_count || 0) + '字 / 目标 ' + (result.target_words || 0) + '字</strong>' + editBtn + '</div>'
        + '<div class="ln-step-output-preview">' + renderMarkdown(result.content || '') + '</div>';
    } else {
      outEl.innerHTML = '<div style="margin-bottom:0.4rem"><strong>' + escapeHtml(_lnWritingStepLabels[stepName] || stepName) + ' · ' + (result.word_count || 0) + '字</strong>' + editBtn + '</div>'
        + '<div class="ln-step-output-preview">' + renderMarkdown(result.content || '') + '</div>';
    }
    if (stepName === 'expand' || stepName === 'polish' || stepName === 'deslop') {
      _lnPopulateDiffSection(row, result);
    }
    // Bind edit button
    if (isEditable && row) {
      var editBtnEl = outEl.querySelector('[data-step-edit]');
      if (editBtnEl) {
        editBtnEl.addEventListener('click', function() {
          _lnEnterStepEditMode(row, stepName, result.content || '');
        });
      }
    }
    _lnBindGateActions(row, _lnViewChapter || 0, stepName);
  }

  async function _lnLoadChapterStepOutput(chNum, stepName) {
    if (!_lnActiveBookId || !chNum) return;
    var row = _lnFindStepRow(stepName);
    if (!row) return;
    var outEl = row.querySelector('[data-step-output]');
    if (!outEl || !outEl.querySelector('.empty')) return;
    try {
      var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName);
      _lnRenderSavedStepOutput(row, stepName, result || {});
    } catch (err) {
      outEl.innerHTML = '<div class="empty" style="padding:0.5rem">读取已完成内容失败：' + escapeHtml(err.message || '') + '</div>';
    }
  }

  async function _lnSkipChapterStep(chNum, stepName) {
    if (!_lnActiveBookId || !chNum) return;
    try {
      var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName + '/skip', { method: 'POST' });
      _lnSetStepStatus(stepName, 'skipped');
      _lnSetStepBodyExpanded(stepName, false);
      toast(result.message || '已跳过', 'success');
    } catch (err) {
      toast('跳过失败：' + err.message, 'error');
    }
  }

  function _lnGateScoreHtml(gate, label, forcePass) {
    gate = gate || {};
    var verdictUpper = String(gate.verdict || gate.overall || '').toUpperCase();
    var fallbackScore = verdictUpper === 'APPROVE' ? 90
      : (verdictUpper === 'REJECT' ? 45 : (verdictUpper === 'PENDING' ? 80 : 72));
    var score = Number(gate.score == null ? fallbackScore : gate.score);
    var passScore = Number(gate.pass_score || 80);
    var forced = !!(forcePass && forcePass.force_passed);
    var strictZhuque = gate.source === 'zhuque_web' || !!gate.required_label;
    var pending = !!gate.pending;
    var passed = strictZhuque ? !!gate.passed : (!!gate.passed || forced);
    var verdict = String(gate.verdict || (passed ? 'APPROVE' : 'CONCERNS')).toUpperCase();
    var cls = passed ? 'approve' : (verdict === 'REJECT' ? 'reject' : (pending ? 'pending' : 'concerns'));
    var verdictLabel = passed ? 'PASS' : (pending ? '待手动复查' : verdict);
    var width = Math.max(0, Math.min(100, score || 0));
    var standardText;
    if (pending) {
      standardText = '本地去 AI 已完成，可继续下一步；如需外部 AI 味检测请自行复查';
    } else {
      standardText = '达标线：' + passScore + '分';
    }
    return '<div class="ln-quality-gate ' + cls + '">'
      + '<div class="ln-quality-gate-top"><strong>' + escapeHtml(label) + '</strong>'
      + '<span class="ln-quality-score">' + Math.round(score) + '</span>'
      + '<span class="ln-review-dim-verdict ' + cls + '">' + escapeHtml(verdictLabel) + '</span>'
      + (forced ? '<span class="ln-review-dim-verdict concerns">强行通过</span>' : '')
      + '</div><div class="ln-quality-bar"><span style="width:' + width + '%"></span><em style="left:' + passScore + '%"></em></div>'
      + '<div class="inbox-meta">' + standardText + (forcePass && forcePass.reason ? ' · ' + escapeHtml(forcePass.reason) : '') + '</div></div>';
  }

  function _lnGateActionsHtml(stepName, gate, forcePass) {
    gate = gate || {};
    if (stepName !== 'review' && stepName !== 'deslop') return '';
    var hasReviewAdvice = false;
    if (stepName === 'review') {
      hasReviewAdvice = (gate.recommendations || []).length > 0;
      Object.keys(gate.dimensions || {}).forEach(function(k) {
        var d = gate.dimensions[k] || {};
        if ((d.findings || []).length || (d.recommendations || []).length) hasReviewAdvice = true;
      });
    }
    if ((gate.passed || ((forcePass && forcePass.force_passed) && !gate.required_label)) && gate.score != null && !hasReviewAdvice) return '';
    var label = (stepName === 'deslop' && gate.pending) ? '继续改一次' : '按建议修改';
    var force = (stepName === 'deslop' && gate.pending) ? '✓ 标记为通过' : '强行通过';
    return '<div class="ln-quality-actions">'
      + '<button class="btn-warning tiny" data-step-revise="' + stepName + '">' + label + '</button>'
      + '<button class="ghost tiny" data-step-force-pass="' + stepName + '">' + force + '</button>'
      + '</div>';
  }

  function _lnRenderList(items) {
    return (items || []).filter(Boolean).map(function(x) { return '<li>' + escapeHtml(String(x)) + '</li>'; }).join('');
  }

  function _lnRenderReviewDimension(info, d) {
    d = d || {};
    var v = String(d.verdict || 'CONCERNS').toUpperCase();
    var vCls = v === 'APPROVE' ? 'approve' : (v === 'REJECT' ? 'reject' : 'concerns');
    var strengths = _lnRenderList(d.strengths || []);
    var findings = _lnRenderList(d.findings || []);
    var recs = _lnRenderList(d.recommendations || []);
    var raw = d.raw ? '<details class="ln-review-raw"><summary>原始审查输出</summary><pre>' + escapeHtml(String(d.raw)) + '</pre></details>' : '';
    return '<div class="ln-review-dim' + (findings ? ' has-issue' : '') + '">'
      + '<h6>' + info[0] + ' ' + escapeHtml(info[1]) + '<span class="ln-review-dim-verdict ' + vCls + '">' + escapeHtml(v) + '</span></h6>'
      + '<div class="inbox-meta" style="font-size:0.74rem;margin-bottom:0.35rem">' + escapeHtml(info[2]) + '</div>'
      + (strengths ? '<div class="ln-review-section strengths"><strong>审查观察</strong><ul>' + strengths + '</ul></div>' : '')
      + (findings ? '<div class="ln-review-section issues"><strong>真正需要修改的问题</strong><ul>' + findings + '</ul></div>' : '<div class="ln-review-clean">未发现明显问题</div>')
      + (recs ? '<div class="ln-review-section recs"><strong>修改建议</strong><ul>' + recs + '</ul></div>' : '')
      + raw
      + '</div>';
  }

  function _lnRenderReviewResult(review, forcePass) {
    if (!review) return '<div class="empty">无审查数据</div>';
    var dims = review.dimensions || review.details || {};
    var names = {
      continuity:          ['', '连续性检查',     '是否承接前文与长期记忆'],
      logic:               ['', '逻辑',           '因果是否成立、是否自洽'],
      plot_progress:       ['', '剧情是否推进',   '本章是否真的把主线推进了'],
      character_integrity: ['', '人设是否崩塌',   '语言、动机、关系是否沿用设定'],
      environment:         ['', '环境是否恰当',   '世界观、场景、时代是否冲突'],
      empathy:             ['', '读者是否能共情', '情绪铺垫、爽点、痛点是否成立']
    };
    var head = _lnGateScoreHtml(review, '审查总分', forcePass);
    var audit = review.revision_audit || {};
    var auditHtml = audit.mode ? '<div class="ln-review-audit">'
      + '<strong>修复验证</strong>'
      + '<span>上轮：' + escapeHtml(String(audit.previous_overall || '?')) + ' · ' + escapeHtml(String(audit.previous_score == null ? '?' : audit.previous_score)) + '分'
      + ' · 问题 ' + escapeHtml(String(audit.previous_issue_count == null ? '?' : audit.previous_issue_count)) + ' 条</span>'
      + '<span>复审：' + escapeHtml(String(audit.new_overall || review.overall || '?')) + ' · ' + escapeHtml(String(audit.new_score == null ? (review.score || '?') : audit.new_score)) + '分'
      + ' · 剩余 ' + escapeHtml(String(audit.remaining_issue_count == null ? '?' : audit.remaining_issue_count)) + ' 条</span>'
      + (audit.remaining_summary ? '<details><summary>剩余问题摘要</summary><pre>' + escapeHtml(audit.remaining_summary) + '</pre></details>' : '')
      + '</div>' : '';
    var body = '<div class="ln-review-dim-grid">' + Object.keys(names).map(function(k) {
      var info = names[k], d = dims[k] || {};
      return _lnRenderReviewDimension(info, d);
    }).join('') + '</div>';
    var topRecs = _lnRenderList(review.recommendations || []);
    var topRecsHtml = topRecs ? '<div class="ln-review-top-recs"><strong>总体建议</strong><ul>' + topRecs + '</ul></div>' : '';
    return head + auditHtml + body + topRecsHtml + _lnGateActionsHtml('review', review, forcePass);
  }

  function _lnBindGateActions(row, chNum, stepName) {
    if (!row) return;
    row.querySelectorAll('[data-step-force-pass]').forEach(function(btn) {
      btn.addEventListener('click', function(e) { e.stopPropagation(); _lnForcePassChapterStep(chNum, stepName); });
    });
    row.querySelectorAll('[data-step-revise]').forEach(function(btn) {
      btn.addEventListener('click', function(e) { e.stopPropagation(); _lnReviseChapterStep(chNum, stepName); });
    });
  }

  async function _lnForcePassChapterStep(chNum, stepName) {
    if (!_lnActiveBookId || !chNum) return;
    var reason = prompt('强行通过原因（可留空）：') || '';
    try {
      await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName + '/force-pass', { method: 'POST', body: { reason: reason || '人工确认可通过' } });
      var row = _lnFindStepRow(stepName);
      if (row) _lnRenderSavedStepOutput(row, stepName, await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName));
      toast('已强行通过 ' + (_lnWritingStepLabels[stepName] || stepName), 'success');
    } catch (err) {
      toast('强行通过失败：' + err.message, 'error');
    }
  }

  async function _lnReviseChapterStep(chNum, stepName) {
    if (!_lnActiveBookId || !chNum) return;
    var extra = prompt('补充修改要求（可留空）：') || '';
    _lnSetStepStatus(stepName, 'running', '修改中');
    try {
      var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName + '/revise', { method: 'POST', body: { prompt: extra } });
      toast(result.message || '已按建议修改', 'success');
      if (stepName === 'review') {
        var reviewRow = _lnFindStepRow(stepName);
        _lnRenderSavedStepOutput(reviewRow, stepName, result || {});
        _lnSetStepStatus(stepName, result && result.review && result.review.passed ? 'done' : 'error',
          result && result.review && result.review.passed ? '✓ 已通过' : '⚠ 需继续修');
      } else {
        var row = _lnFindStepRow(stepName);
        _lnRenderSavedStepOutput(row, stepName, result || {});
        if (stepName === 'expand' || stepName === 'polish' || stepName === 'deslop') {
          _lnPopulateDiffSection(row, result || {});
        }
        _lnSetStepStatus(stepName, 'done');
      }
    } catch (err) {
      _lnSetStepStatus(stepName, 'error', '✕ ' + (err.message || '失败').slice(0, 40));
      toast('按建议修改失败：' + err.message, 'error');
    }
  }

  async function _lnRunChapterStep(chNum, stepName, options) {
    if (!_lnActiveBookId || !chNum) return;
    options = options || {};
    _lnSetStepStatus(stepName, 'running');
    _lnSetStepBodyExpanded(stepName, true);
    try {
      var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName, {
        method: 'POST',
        body: options.force ? { force: true } : {}
      });
      var row = _lnFindStepRow(stepName);
      var outEl = row ? row.querySelector('[data-step-output]') : null;
      if (outEl) {
        if (stepName === 'review') {
          outEl.innerHTML = _lnRenderReviewResult(result.review || {}, result.force_pass || {});
        } else if (stepName === 'deslop') {
          outEl.innerHTML = '';
        } else if (stepName === 'finalize') {
          outEl.innerHTML = '<div style="margin-bottom:0.5rem"><strong>✓ 已成稿 · ' + (result.final_words || 0) + '字</strong>'
            + '<br><span class="inbox-meta">' + escapeHtml(result.draft_path || '') + '</span></div>'
            + (result.content ? '<div class="ln-step-output-preview">' + renderMarkdown(result.content || '') + '</div>' : '');
          toast('第' + chNum + '章已成稿，共' + (result.final_words || 0) + '字。', 'success');
        } else if (stepName === 'draft') {
          // refresh manifest with actually-sent data if returned
          var manifestEl = row.querySelector('[data-step-manifest]');
          if (manifestEl && result.llm_context) {
            manifestEl.innerHTML = '<div class="ln-step-material-grid" style="padding:0">'
              + result.llm_context.map(function(it) {
                  return _lnContextItemHtml(it.label, it.present ? 'x'.repeat(it.chars || 1) : '', !!it.required);
                }).join('')
              + '</div>';
          }
          outEl.innerHTML = '<div style="margin-bottom:0.4rem"><strong>初稿 · ' + (result.word_count || 0) + '字 / 目标 ' + (result.target_words || 0) + '字</strong></div>'
            + '<div class="ln-step-output-preview">' + renderMarkdown(result.content || '') + '</div>';
        } else if (result.skipped && stepName === 'expand') {
          outEl.innerHTML = '<div style="margin-bottom:0.4rem"><strong>已自动跳过扩写 · 初稿 ' + (result.word_count || 0) + '字</strong>'
            + '<br><span class="inbox-meta">' + escapeHtml(result.message || '初稿已达到 3000 字，无需扩写。') + '</span></div>'
            + '<div class="ln-step-output-preview">' + renderMarkdown(result.content || '') + '</div>';
        } else {
          outEl.innerHTML = '<div style="margin-bottom:0.4rem"><strong>' + escapeHtml(_lnWritingStepLabels[stepName] || stepName) + ' · ' + (result.word_count || 0) + '字</strong></div>'
            + '<div class="ln-step-output-preview">' + renderMarkdown(result.content || '') + '</div>';
        }
        if (stepName === 'expand' || stepName === 'polish' || stepName === 'deslop') {
          _lnPopulateDiffSection(row, result);
        }
        _lnBindGateActions(row, chNum, stepName);
      }
      _lnSetStepStatus(stepName, result.skipped ? 'skipped' : 'done', result.skipped ? '已自动跳过' : null);
      if (result.message) toast(result.message, result.skipped ? 'info' : 'success');
      // Mark earlier steps done too (since running this implies they exist)
      var idx = _lnWritingStepOrder.indexOf(stepName);
      for (var i = 0; i < idx; i++) {
        var prevRow = _lnFindStepRow(_lnWritingStepOrder[i]);
        if (
          prevRow
          && !prevRow.classList.contains('status-done')
          && !prevRow.classList.contains('status-skipped')
        ) {
          _lnSetStepStatus(_lnWritingStepOrder[i], 'done');
        }
      }
      if (stepName === 'finalize') {
        await loadWritingWorkbench();
      }
    } catch (err) {
      _lnSetStepStatus(stepName, 'error', '✕ ' + (err.message || '失败').slice(0, 40));
      toast(_lnWritingStepLabels[stepName] + '失败：' + err.message, 'error');
    }
  }

  async function _lnRunChapterStep(chNum, stepName, options) {
    if (!_lnActiveBookId || !chNum) return;
    options = options || {};
    _lnSetStepStatus(stepName, 'running');
    _lnSetStepBodyExpanded(stepName, true);
    try {
      var startResp = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName + '/start', {
        method: 'POST',
        body: options.force ? { force: true } : {}
      });
      if (!startResp.already_running) {
        toast(startResp.detail || ((_lnWritingStepLabels[stepName] || stepName) + '已启动'), 'info');
      }
      _lnStartChapterStepPolling(chNum, stepName, { silent: true });
    } catch (err) {
      _lnSetStepStatus(stepName, 'error', '✕ ' + (err.message || '失败').slice(0, 40));
      toast((_lnWritingStepLabels[stepName] || stepName) + '失败：' + err.message, 'error');
    }
  }

  async function _lnRenderChapterExpand(chNum) {
    var list = document.getElementById('ln-chapter-list');
    if (!list) return;
    _lnStopAllChapterStepPollers();
    // Remove any existing expand
    list.querySelectorAll('.ln-chapter-expand').forEach(function(e) { e.remove(); });
    var card = list.querySelector('.ln-chapter-card[data-ln-ch="' + chNum + '"]');
    if (!card) return;

    // 「单章详情」视图：隐藏全部章节卡（包括当前章），只显示 expand 详情面板。
    // 通过「↩ 返回章节列表」按钮恢复多集视图。
    list.classList.add('solo-view');
    list.querySelectorAll('.ln-chapter-card').forEach(function(c) {
      c.style.display = 'none';
    });

    var expand = document.createElement('div');
    expand.className = 'ln-chapter-expand';
    expand.dataset.lnChExpand = String(chNum);
    expand.innerHTML = '<div class="empty" style="padding:0.5rem">加载中…</div>';
    card.insertAdjacentElement('afterend', expand);

    var ch = {};
    var available = [];
    var skipped = [];
    var progressRows = [];
    try {
      var chData = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters/' + chNum);
      ch = chData.chapter || {};
    } catch (_e) {}
    try {
      var sd = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step-status');
      (sd.steps_available || []).forEach(function(st) {
        if (st && st.step) available.push(st.step);
        if (st && st.step && st.skipped) skipped.push(st.step);
      });
      progressRows = sd.steps_progress || [];
      if (sd.chapter_status === 'draft' && available.indexOf('finalize') < 0) available.push('finalize');
    } catch (_e) {}

    var statusMap = _lnChapterStepStatus(ch, available, skipped, progressRows);
    var statusBadge = (ch.status === 'draft' || ch.status === 'published') ? '✓ 已成稿' : (ch.status === 'writing' ? '写作中' : '待写');
    var reviewBadge = ch.review_status ? ' · 审查：<strong>' + escapeHtml(ch.review_status) + '</strong>' : '';
    var rowsHtml = _lnWritingSteps.map(function(step) {
      var rowStatus = statusMap[step.id] || 'pending';
      return _lnStepRowHtml(step, rowStatus);
    }).join('');

    var safeTitle = escapeHtml(ch.title || '未命名');
    expand.innerHTML = ''
      + '<div class="ln-chapter-expand-head">'
      + '  <div class="ln-chapter-title-block">'
      + '    <div class="ln-chapter-title-line">'
      + '      <span class="ln-chapter-title-prefix">第' + chNum + '章</span>'
      + '      <span class="ln-chapter-title-text" data-ch-title>' + safeTitle + '</span>'
      + '      <button class="ln-edit-btn" data-ch-title-edit title="手动修改标题">改标题</button>'

      + '      <button class="ln-ai-name-btn" data-ch-title-ai title="让 AI 根据正文/大纲生成标题">AI 命名</button>'
      + '    </div>'
      + '    <div class="meta">' + statusBadge + ' · ' + (ch.actual_words || 0) + '字' + reviewBadge + '</div>'
      + '  </div>'
      + '  <div class="ln-chapter-expand-actions">'
      + '    <button class="ghost tiny" data-ln-prev>◀ 上一章</button>'
      + '    <button class="ghost tiny" data-ln-next>下一章 ▶</button>'
      + '    <button class="ln-chapter-expand-close" data-ln-collapse>↩ 返回章节列表</button>'
      + '  </div>'
      + '</div>'
      + '<div class="ln-step-rows">' + rowsHtml + '</div>';

    expand.querySelector('[data-ln-collapse]').addEventListener('click', function() {
      _lnStopAllChapterStepPollers();
      _lnViewChapter = 0;
      _lnPersistWorkspaceState();
      list.classList.remove('solo-view');
      list.querySelectorAll('.ln-chapter-card').forEach(function(c) { c.style.display = ''; });
      expand.remove();
      card.classList.remove('active');
    });
    var pBtn = expand.querySelector('[data-ln-prev]');
    if (pBtn) pBtn.addEventListener('click', function() { navigateChapter(-1); });
    var nBtn = expand.querySelector('[data-ln-next]');
    if (nBtn) nBtn.addEventListener('click', function() { navigateChapter(1); });

    var editBtn = expand.querySelector('[data-ch-title-edit]');
    if (editBtn) editBtn.addEventListener('click', function() { _lnEditChapterTitle(chNum, ch.title || ''); });
    var aiBtn = expand.querySelector('[data-ch-title-ai]');
    if (aiBtn) aiBtn.addEventListener('click', function() { _lnAiGenChapterTitle(chNum); });

    expand.querySelectorAll('[data-step-row]').forEach(function(row) {
      var stepId = row.dataset.stepRow;
      var runBtn = row.querySelector('[data-step-run]');
      if (runBtn) runBtn.addEventListener('click', function() { _lnRunChapterStep(chNum, stepId); });
      var forceExpandBtn = row.querySelector('[data-step-force-expand]');
      if (forceExpandBtn) forceExpandBtn.addEventListener('click', function() { _lnRunChapterStep(chNum, stepId, { force: true }); });
      var skipBtn = row.querySelector('[data-step-skip]');
      if (skipBtn) skipBtn.addEventListener('click', function() { _lnSkipChapterStep(chNum, stepId); });
      var promptBtn = row.querySelector('[data-step-prompt]');
      if (promptBtn) promptBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        _showPromptModal(stepId);
      });
      var rowHead = row.querySelector('.ln-step-row-head');
      if (rowHead) rowHead.addEventListener('click', function(e) {
        if (e.target.closest('button') || e.target.closest('a')) return;
        var willOpen = !row.classList.contains('expanded');
        _lnSetStepBodyExpanded(stepId, willOpen);
        if (willOpen && (row.classList.contains('status-done') || row.classList.contains('status-skipped'))) _lnLoadChapterStepOutput(chNum, stepId);
      });
      // 复制全文（仅 deslop 行有）
      var copyBtn = row.querySelector('[data-step-copy-all]');
      if (copyBtn) copyBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        _lnCopyStepContent(chNum, stepId, copyBtn);
      });
      // 历史版本 details 展开时按需拉取（首次展开才请求）
      var historyDetails = row.querySelector('details[data-step-section="history"]');
      if (historyDetails) historyDetails.addEventListener('toggle', function() {
        if (!historyDetails.open) return;
        if (historyDetails.dataset.loaded === '1') return;
        _lnLoadStepHistory(chNum, stepId, historyDetails);
      });
    });

    // manifest 已迁移到「链路」tab，此处不再自动拉 /context/N（避免卡顿）

    (progressRows || []).forEach(function(item) {
      if (!item || !item.step) return;
      if (item.status === 'starting' || item.status === 'running') {
        _lnSetStepStatus(item.step, 'running');
        _lnStartChapterStepPolling(chNum, item.step, { silent: true });
      } else if (item.status === 'error' || item.status === 'cancelled') {
        _lnSetStepStatus(item.step, 'error', '✕ ' + String(item.detail || '失败').slice(0, 40));
      } else if (item.status === 'skipped') {
        _lnSetStepStatus(item.step, 'skipped');
      }
    });

    expand.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  }

  async function _lnCopyStepContent(chNum, stepName, btn) {
    if (!_lnActiveBookId || !chNum) return;
    try {
      var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName);
      var text = String((result && result.content) || '');
      if (!text) { toast('没有可复制的内容', 'error'); return; }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      toast('已复制 ' + text.length + ' 字', 'success');
      if (btn) { var old = btn.textContent; btn.textContent = '✓ 已复制'; setTimeout(function() { btn.textContent = old; }, 1800); }
    } catch (err) {
      toast('复制失败：' + (err.message || err), 'error');
    }
  }

  async function _lnLoadStepHistory(chNum, stepName, detailsEl) {
    if (!_lnActiveBookId || !chNum || !detailsEl) return;
    var holder = detailsEl.querySelector('[data-step-history]');
    if (!holder) return;
    holder.innerHTML = '<div class="empty" style="padding:0.5rem">加载中...</div>';
    try {
      var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName + '/history');
      var versions = (result && result.versions) || [];
      if (!versions.length) {
        holder.innerHTML = '<div class="empty" style="padding:0.5rem">暂无历史版本（重做后会自动归档）</div>';
        detailsEl.dataset.loaded = '1';
        return;
      }
      holder.innerHTML = '<div class="empty" style="padding:0.5rem">正在加载 ' + versions.length + ' 个版本的内容...</div>';
      var bookId = _lnActiveBookId;
      var contents = await Promise.all(versions.map(function(v) {
        return api('/api/long-novel/books/' + bookId + '/write-chapter/' + chNum + '/step/' + stepName + '/history/' + encodeURIComponent(v.id))
          .then(function(r) { return {ok: true, content: String((r && r.content) || ''), word_count: (r && r.word_count) || 0}; })
          .catch(function(err) { return {ok: false, error: String(err.message || err)}; });
      }));
      var html = '<div class="ln-pl-subhead" style="margin-bottom:0.4rem;color:var(--muted,#9aa0a6);font-size:0.78rem">共 ' + versions.length + ' 个历史版本（从新到旧 · 横向滚动浏览）</div>';
      html += '<div class="ln-history-row" style="display:flex;flex-direction:row;gap:0.6rem;overflow-x:auto;overflow-y:hidden;padding-bottom:0.5rem;scroll-snap-type:x mandatory">';
      versions.forEach(function(v, i) {
        var sizeKb = (v.size / 1024).toFixed(1);
        var tag = i === 0 ? '<span style="background:rgba(45,164,78,0.25);color:#7ee7a1;padding:1px 6px;border-radius:3px;font-size:0.7rem">最新</span>' : '';
        var c = contents[i] || {};
        var bodyHtml = '';
        if (c.ok) {
          var paras = String(c.content).split(/\n+/).map(function(s) { return s.trim(); }).filter(function(s) { return s.length > 0; });
          var paraHtml = paras.map(function(p) { return '<p>' + escapeHtml(p) + '</p>'; }).join('') || '<div class="empty" style="padding:0.3rem">（内容为空）</div>';
          bodyHtml = '<div class="ln-history-meta" style="font-size:0.75rem;color:var(--muted,#9aa0a6);margin-bottom:0.3rem">字数 ' + c.word_count + ' · ' + paras.length + ' 段</div>'
            + '<div class="ln-history-content" style="flex:1;overflow:auto;background:rgba(0,0,0,0.25);color:var(--text,inherit);padding:0.5rem 0.75rem;border-radius:4px;line-height:1.7">' + paraHtml + '</div>';
        } else {
          bodyHtml = '<div class="empty" style="padding:0.3rem;color:var(--danger,#c00)">加载失败：' + escapeHtml(c.error || '未知错误') + '</div>';
        }
        html += '<div class="ln-history-item" style="flex:0 0 380px;scroll-snap-align:start;display:flex;flex-direction:column;height:500px;border:1px solid rgba(255,255,255,0.12);border-radius:6px;background:rgba(255,255,255,0.03);overflow:hidden">'
          + '<div class="ln-history-head" style="padding:0.4rem 0.6rem;display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;background:rgba(255,255,255,0.04);color:var(--text,inherit);flex-shrink:0">'
          +   '<span style="font-family:monospace;font-size:0.78rem;color:var(--text,inherit);font-weight:600">' + escapeHtml(v.id) + '</span>'
          +   tag
          +   '<span style="font-size:0.72rem;color:var(--muted,#9aa0a6);width:100%;display:block">' + escapeHtml(String(v.mtime || '')) + ' · ' + sizeKb + ' KB</span>'
          + '</div>'
          + '<div style="padding:0.5rem 0.75rem;flex:1;display:flex;flex-direction:column;overflow:hidden">' + bodyHtml + '</div>'
          + '</div>';
      });
      html += '</div>';
      holder.innerHTML = html;
      detailsEl.dataset.loaded = '1';
    } catch (err) {
      holder.innerHTML = '<div class="empty" style="padding:0.5rem;color:var(--danger,#c00)">加载失败：' + escapeHtml(String(err.message || err)) + '</div>';
    }
  }

  function _lnEditChapterTitle(chNum, current) {
    var modal = document.getElementById('title-edit-modal');
    var input = document.getElementById('title-edit-input');
    if (!modal || !input) return;
    modal.style.display = 'flex';
    input.value = String(current || '');
    input.focus();
    input.select();

    var confirmBtn = document.getElementById('title-edit-confirm');
    var cancelBtn = document.getElementById('title-edit-cancel');

    function cleanup() {
      modal.style.display = 'none';
      confirmBtn.removeEventListener('click', onConfirm);
      cancelBtn.removeEventListener('click', onCancel);
      modal.removeEventListener('click', onBackdrop);
      input.removeEventListener('keydown', onKey);
    }

    async function onConfirm() {
      var next = String(input.value || '').trim();
      if (!next) { toast('标题不能为空', 'error'); return; }
      cleanup();
      try {
        var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters/' + chNum, {
          method: 'PUT', body: { title: next }
        });
        var titleNew = (result && result.chapter && result.chapter.title) || next;
        _lnUpdateChapterTitleInUi(chNum, titleNew);
        toast('已更新章节标题', 'success');
      } catch (err) {
        toast('修改失败：' + (err.message || err), 'error');
      }
    }

    function onCancel() { cleanup(); }

    function onBackdrop(e) { if (e.target === modal) cleanup(); }

    function onKey(e) {
      if (e.key === 'Enter') { e.preventDefault(); onConfirm(); }
      else if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
    }

    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
    modal.addEventListener('click', onBackdrop);
    input.addEventListener('keydown', onKey);
  }

  function _lnEnterStepEditMode(row, stepName, content) {
    var outEl = row.querySelector('[data-step-output]');
    if (!outEl) return;

    // Check if edit area already exists - prevent duplicates
    var existingEditArea = outEl.querySelector('.ln-step-edit-area');
    if (existingEditArea) {
      existingEditArea.querySelector('textarea').focus();
      return;
    }

    var previewEl = outEl.querySelector('.ln-step-output-preview');
    if (!previewEl) return;

    // Hide preview, show textarea
    previewEl.style.display = 'none';
    var editArea = document.createElement('div');
    editArea.className = 'ln-step-edit-area';
    editArea.innerHTML = '<textarea class="ln-step-edit-textarea">' + escapeHtml(content) + '</textarea>'
      + '<div class="ln-step-edit-actions">'
      + '  <button class="btn-primary tiny" data-step-edit-save>保存</button>'
      + '  <button class="ghost tiny" data-step-edit-cancel>取消</button>'
      + '</div>';
    outEl.appendChild(editArea);

    var textarea = editArea.querySelector('textarea');
    var saveBtn = editArea.querySelector('[data-step-edit-save]');
    var cancelBtn = editArea.querySelector('[data-step-edit-cancel]');
    textarea.focus();

    saveBtn.addEventListener('click', async function() {
      var newContent = textarea.value;
      saveBtn.disabled = true;
      saveBtn.textContent = '保存中…';

      // Get chapter number from expand panel's data attribute
      var expandPanel = row.closest('.ln-chapter-expand[data-ln-ch-expand]');
      var chNum = expandPanel ? expandPanel.dataset.lnChExpand : _lnViewChapter;

      if (!chNum || chNum === '0') {
        toast('无法确定章节号', 'error');
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
        return;
      }

      try {
        await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + stepName + '/content', {
          method: 'PUT',
          body: { content: newContent }
        });
        // Update preview
        previewEl.innerHTML = renderMarkdown(newContent);
        previewEl.style.display = '';
        editArea.remove();
        toast('内容已保存', 'success');
      } catch (err) {
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
        toast('保存失败：' + (err.message || err), 'error');
      }
    });

    cancelBtn.addEventListener('click', function() {
      previewEl.style.display = '';
      editArea.remove();
    });
  }

  async function _lnAiGenChapterTitle(chNum) {
    var btn = document.querySelector('.ln-chapter-expand[data-ln-ch-expand="' + chNum + '"] [data-ch-title-ai]');
    if (btn) { btn.disabled = true; btn.dataset.oldText = btn.textContent; btn.textContent = 'AI 生成中…'; }
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters/' + chNum + '/generate-title', { method: 'POST' });
      var suggested = String(data && data.title || '').trim();
      if (!suggested) { toast('AI 没返回有效标题，请稍后重试', 'error'); return; }
      if (!await showConfirmAsync('AI 建议标题：「' + suggested + '」\n\n是否采用？（取消则不改）')) return;
      var saved = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters/' + chNum, {
        method: 'PUT', body: { title: suggested }
      });
      var titleNew = (saved && saved.chapter && saved.chapter.title) || suggested;
      _lnUpdateChapterTitleInUi(chNum, titleNew);
      toast('已采用 AI 生成的章节标题', 'success');
    } catch (err) {
      toast('AI 命名失败：' + (err.message || err), 'error');
    } finally {
      if (btn) { btn.disabled = false; if (btn.dataset.oldText) btn.textContent = btn.dataset.oldText; }
    }
  }

  function _lnUpdateChapterTitleInUi(chNum, newTitle) {
    var titleEl = document.querySelector('.ln-chapter-expand[data-ln-ch-expand="' + chNum + '"] [data-ch-title]');
    if (titleEl) titleEl.textContent = newTitle;
    var card = document.querySelector('.ln-chapter-card[data-ln-ch="' + chNum + '"] .ln-chapter-card-title');
    if (card) card.textContent = newTitle;
  }

  async function loadWritingWorkbench() {
    ensureWritingPanelLayout();
    if (!_lnActiveBookId) {
      var titleEl = document.getElementById('ln-ws-book-title');
      if (titleEl) titleEl.textContent = '请先在书库中选择一本书';
      return;
    }
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = data.book || {};
      var bookTitleEl = document.getElementById('ln-ws-book-title');
      var progressEl = document.getElementById('ln-ws-progress');
      if (bookTitleEl) bookTitleEl.textContent = '' + escapeHtml(book.title || '');
      if (progressEl) progressEl.textContent = '第 ' + (book.current_chapter || 0) + '/' + (book.target_chapters || 0) + ' 章 · ' + escapeHtml(book.genre || '');

      var chData = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters');
      var chapters = chData.chapters || [];
      if (!chapters.length && book.status === 'setup' && await _lnCanFinalizeFromArtifacts()) {
        await _lnFinalizeSetupFromArtifacts();
        chData = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters');
        chapters = chData.chapters || [];
      }

      var list = $('ln-chapter-list');
      // 重置一下 solo-view，防止 navigateChapter 切换章节后样式残留
      list.classList.remove('solo-view');
      if (!chapters.length) {
        list.innerHTML = '<div class="empty">还没有待写章节。请先在「大纲」tab 生成章节细纲，或回到「全自动生成」整理章节队列。</div>';
        return;
      }

      list.innerHTML = chapters.map(_lnChapterCardHtml).join('');

      list.querySelectorAll('[data-ln-ch]').forEach(function(card) {
        card.addEventListener('click', function() {
          var ch = parseInt(card.dataset.lnCh, 10);
          // 改为单章详情视图：再次点击当前卡不再折叠，由「↩ 返回章节列表」按钮负责回到列表。
          _lnViewChapter = ch;
          _lnPersistWorkspaceState();
          list.querySelectorAll('.ln-chapter-card.active').forEach(function(c) { c.classList.remove('active'); });
          card.classList.add('active');
          _lnRenderChapterExpand(ch);
        });
      });

      // Re-expand the previously active chapter (e.g. after refresh post-finalize)
      if (_lnViewChapter) {
        var activeCard = list.querySelector('.ln-chapter-card[data-ln-ch="' + _lnViewChapter + '"]');
        if (activeCard) {
          activeCard.classList.add('active');
          _lnRenderChapterExpand(_lnViewChapter);
        }
      }
    } catch (err) {
      toast('加载正文工作台失败：' + err.message, 'error');
    }
  }

  function navigateChapter(delta) {
    var cards = Array.prototype.slice.call(document.querySelectorAll('#ln-chapter-list [data-ln-ch]'));
    var nums = cards.map(function(c) { return parseInt(c.dataset.lnCh, 10); }).filter(Boolean).sort(function(a, b) { return a - b; });
    if (!nums.length) return;
    var idx = nums.indexOf(_lnViewChapter);
    if (idx < 0) idx = 0;
    idx = Math.max(0, Math.min(nums.length - 1, idx + delta));
    var nextCh = nums[idx];
    if (nextCh === _lnViewChapter) return;
    _lnViewChapter = nextCh;
    _lnPersistWorkspaceState();
    var list = document.getElementById('ln-chapter-list');
    if (list) {
      list.querySelectorAll('.ln-chapter-card.active').forEach(function(c) { c.classList.remove('active'); });
      var newCard = list.querySelector('.ln-chapter-card[data-ln-ch="' + nextCh + '"]');
      if (newCard) newCard.classList.add('active');
    }
    _lnRenderChapterExpand(nextCh);
  }

  async function writeNextChapter() {
    if (!_lnActiveBookId) { toast('请先选择一本书', 'error'); return; }
    try {
      var bkData = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = bkData.book || {};
      if (book.status === 'setup') {
        if (await _lnCanFinalizeFromArtifacts()) {
          toast('设定和大纲已完成，正在整理待写章节队列...', 'info');
          await _lnFinalizeSetupFromArtifacts();
        } else {
          if (!await showConfirmAsync('这本书还没完成开书流程。是否先运行设定？\n\n设定页会生成：题材定位、世界观、角色设计；随后到「大纲」tab 继续生成大纲、卷纲、章节细纲。')) return;
          _lnRunSetupPhases();
          return;
        }
      }
      var nextData = await api('/api/long-novel/books/' + _lnActiveBookId + '/next-chapter');
      if (!nextData.chapter) {
        toast('所有章节都已成稿。需要继续写时，到「大纲」tab 点击「追加章节」。', 'info');
        return;
      }
      _lnViewChapter = nextData.chapter.chapter_number;
      await loadWritingWorkbench();
    } catch (err) {
      toast('错误：' + err.message, 'error');
    }
  }

  // Legacy no-ops kept to avoid breaking any external references
  function showStepControls() {}
  function hideStepControls() {}
  async function checkStepStatus() {}
  function updateStepButtons() {}
  async function runStep(stepName) {
    if (_lnViewChapter) await _lnRunChapterStep(_lnViewChapter, stepName);
  }


  var _lnSetupPhases = [
    {id: 'premise', icon: '', label: '题材定位'},
    {id: 'world', icon: '', label: '世界观'},
    {id: 'characters', icon: '', label: '角色设计'},
    {id: 'factions', icon: '', label: '势力'},
    {id: 'relations', icon: '', label: '关系'}
  ];
  var _lnOutlinePhases = [
    {id: 'outline', icon: '', label: '大纲'},
    {id: 'volume_outline', icon: '', label: '卷纲'},
    {id: 'chapter_outlines', icon: '', label: '章节细纲'}
  ];
  function _lnAllSetupPhases() {
    return _lnSetupPhases.concat(_lnOutlinePhases);
  }
  function _lnFindPhase(phaseId) {
    return _lnAllSetupPhases().find(function(p) { return p.id === phaseId; });
  }
  function _lnIsOutlinePhase(phaseId) {
    return _lnOutlinePhases.some(function(p) { return p.id === phaseId; });
  }

  function _lnBindSetupStripActions(stripEl) {
    if (!stripEl) return;
    stripEl.querySelectorAll('[data-ln-prompt-view]').forEach(function(btn) {
      btn.addEventListener('click', function(e) { e.stopPropagation(); _showPromptModal(btn.dataset.lnPromptView); });
    });
    stripEl.querySelectorAll('[data-ln-retry]').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        var chip = btn.closest('[data-ln-phase]');
        if (chip) _lnRetryPhase(chip.dataset.lnPhase);
      });
    });
  }

  function _lnSetSetupPreviewAction(html) {
    var actionEl = document.getElementById('ln-setup-preview-actions');
    if (!actionEl) return;
    actionEl.innerHTML = html || '';
    var retryBtn = actionEl.querySelector('[data-ln-preview-retry]');
    if (retryBtn) retryBtn.addEventListener('click', function() { _lnRetryPhase(retryBtn.dataset.lnPreviewRetry); });
    var runBtn = actionEl.querySelector('[data-ln-preview-run]');
    if (runBtn) runBtn.addEventListener('click', function() { _lnRunSinglePhase(runBtn.dataset.lnPreviewRun); });
    var refreshBtn = actionEl.querySelector('[data-ln-preview-refresh]');
    if (refreshBtn) refreshBtn.addEventListener('click', function() { _lnShowSetupChipPreview(refreshBtn.dataset.lnPreviewRefresh); });
    var fileRegenBtn = actionEl.querySelector('[data-ln-preview-file-regen]');
    if (fileRegenBtn) fileRegenBtn.addEventListener('click', function() {
      _lnRegenerateArtifactFile(fileRegenBtn.dataset.lnPreviewFileRegen, document.getElementById('ln-chip-fileview'));
    });
    var editBtn = actionEl.querySelector('[data-ln-preview-file-edit]');
    if (editBtn) editBtn.addEventListener('click', function() {
      var relPath = editBtn.dataset.lnPreviewFileEdit;
      var container = document.getElementById('ln-chip-fileview');
      if (container) _lnStartEditFromCurrent(relPath, container);
    });
  }

  function _lnAskAdditionalPrompt(label) {
    return window.prompt('生成「' + label + '」前，可以添加本次补充提示词。留空则按默认提示词生成。', '') || '';
  }

  function _lnSetupFileActionsHtml(relPath) {
    return '<button class="ghost tiny" data-ln-preview-file-edit="' + escapeHtml(relPath) + '">编辑当前文件</button> '
      + '<button class="btn-warning tiny" data-ln-preview-file-regen="' + escapeHtml(relPath) + '">重新生成当前文件</button>';
  }

  var _LN_SETUP_PHASE_LABELS = {
    premise: '题材定位', world: '世界观', characters: '角色设计',
    factions: '势力', relations: '关系'
  };

  async function _lnShowSetupChipPreview(phaseId) {
    var label = _LN_SETUP_PHASE_LABELS[phaseId] || phaseId;
    var previewEl = document.getElementById('ln-setup-preview');
    var titleEl = document.getElementById('ln-setup-preview-title');
    var contentEl = document.getElementById('ln-setup-preview-content');
    if (previewEl) previewEl.style.display = '';
    _lnSetSetupPreviewAction('');
    if (titleEl) titleEl.textContent = '' + label;
    if (contentEl) contentEl.textContent = '加载中…';

    var st = 'pending', detail = '';
    try {
      var sResp = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/' + phaseId + '/status');
      st = sResp.status || 'pending';
      detail = sResp.detail || '';
    } catch (_e) {}

    if (st === 'error') {
      if (titleEl) titleEl.textContent = '✕ ' + label + ' — 生成失败';
      if (contentEl) contentEl.innerHTML = '<div style="padding:0.5rem;border-left:3px solid var(--danger);margin-bottom:0.75rem">错误：' + escapeHtml(detail) + '</div>';
      _lnSetSetupPreviewAction('<button class="btn-warning tiny" data-ln-preview-retry="' + phaseId + '">重新生成「' + escapeHtml(label) + '」</button>');
      return;
    }
    if (st === 'running' || st === 'starting') {
      if (titleEl) titleEl.textContent = '' + label + ' — 正在生成中…';
      if (contentEl) contentEl.textContent = detail || 'AI正在生成，请耐心等待…';
      return;
    }

    // pending / cancelled / done — try to load files anyway, since old books may have artifacts even when status is pending
    var files = [];
    try {
      var fr = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-files?phase=' + phaseId);
      files = fr.files || [];
    } catch (_e) {}

    if (st !== 'done' && !files.length) {
      var stateLabel = st === 'cancelled' ? '已中断' : '尚未生成';
      var btnLabel = st === 'cancelled' ? '重新生成「' + label + '」' : '▶ 开始生成「' + label + '」';
      if (titleEl) titleEl.textContent = '' + label + ' — ' + stateLabel;
      if (contentEl) contentEl.innerHTML = '<div style="padding:0.5rem;border-left:3px solid var(--muted);margin-bottom:0.75rem">' + escapeHtml(detail || '该阶段' + stateLabel + '，点击右上角按钮开始生成') + '</div>';
      _lnSetSetupPreviewAction('<button class="btn-primary tiny" data-ln-preview-run="' + phaseId + '">' + escapeHtml(btnLabel) + '</button>');
      return;
    }

    // We have files; render list (if >1) + preview area
    if (!files.length) {
      if (contentEl) contentEl.innerHTML = '<div class="empty" style="padding:0.5rem">阶段已完成，但未找到产出文件</div>';
      _lnSetSetupPreviewAction('<button class="btn-warning tiny" data-ln-preview-retry="' + phaseId + '">重新生成「' + escapeHtml(label) + '」</button>');
      return;
    }

    _lnRenderSetupChipFiles(phaseId, label, files, st);
  }

  async function _lnShowOutlineChipPreview(phaseId) {
    var fileMap = {
      outline:['大纲/大纲.md','大纲'],
      volume_outline:['大纲/卷纲_第一卷.md','卷纲'],
      chapter_outlines:['大纲','章节细纲']
    };
    var info = fileMap[phaseId];
    if (!info) return;
    var label = info[1];
    var titleEl = document.getElementById('ln-outline-preview-title');
    var contentEl = document.getElementById('ln-outline-preview-content');
    _lnSetOutlinePreviewAction('');
    var st = 'pending', detail = '';
    try {
      var sResp = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/' + phaseId + '/status');
      st = sResp.status || 'pending';
      detail = sResp.detail || '';
    } catch (_e) {}
    if (contentEl) contentEl.classList.remove('empty');
    var files = [];
    try {
      var fr = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-files?phase=' + phaseId);
      files = fr.files || [];
    } catch (_e2) {}
    if (files.length) {
      if (titleEl) titleEl.textContent = '' + label;
      if (contentEl) contentEl.textContent = '加载中…';
      _lnRenderOutlineChipFiles(phaseId, label, files, st);
    } else if (st === 'error') {
      if (titleEl) titleEl.textContent = '✕ ' + label + ' — 生成失败';
      if (contentEl) contentEl.innerHTML = '<div style="padding:0.5rem;border-left:3px solid var(--danger);margin-bottom:0.75rem">错误：' + escapeHtml(detail) + '</div>';
      _lnSetOutlinePreviewAction('<button class="btn-warning tiny" data-ln-preview-retry="' + phaseId + '">重新生成「' + escapeHtml(label) + '」</button>');
    } else if (st === 'running' || st === 'starting') {
      if (titleEl) titleEl.textContent = '' + label + ' — 正在生成中…';
      if (contentEl) contentEl.textContent = detail || 'AI正在生成，请耐心等待…';
    } else {
      var stateLabel = st === 'cancelled' ? '已中断' : '尚未生成';
      var btnLabel = st === 'cancelled' ? '重新生成「' + label + '」' : '▶ 开始生成「' + label + '」';
      if (titleEl) titleEl.textContent = '' + label + ' — ' + stateLabel;
      if (contentEl) contentEl.innerHTML = '<div style="padding:0.5rem;border-left:3px solid var(--muted);margin-bottom:0.75rem">' + escapeHtml(detail || '该阶段' + stateLabel + '，点击右上角按钮开始生成') + '</div>';
      _lnSetOutlinePreviewAction('<button class="btn-primary tiny" data-ln-preview-run="' + phaseId + '">' + escapeHtml(btnLabel) + '</button>');
    }
  }

  function _lnRenderSetupChipFiles(phaseId, label, files, status) {
    var titleEl = document.getElementById('ln-setup-preview-title');
    var contentEl = document.getElementById('ln-setup-preview-content');
    if (!contentEl) return;

    // Pick default file: first non-index entry
    var defaultIdx = 0;
    for (var i = 0; i < files.length; i++) {
      if (!files[i].is_index) { defaultIdx = i; break; }
    }

    var stIcon = status === 'done' ? '✓' : (status === 'cancelled' ? '' : '');
    if (titleEl) titleEl.textContent = stIcon + ' ' + label + ' · ' + files.length + ' 个文件';

    var listHtml = '<div class="ln-chip-filelist">' + files.map(function(f, idx) {
      var icon = f.is_index ? '' : '';
      var sizeKb = (f.bytes / 1024).toFixed(1);
      var active = idx === defaultIdx ? ' active' : '';
      return '<button class="ln-chip-fileitem' + active + '" data-ln-file-rel="' + escapeHtml(f.path) + '" data-ln-file-label="' + escapeHtml(f.name) + '" title="' + escapeHtml(f.path) + '">'
        + icon + ' ' + escapeHtml(f.name)
        + '<span class="ln-chip-filesize">' + sizeKb + 'K</span>'
        + '</button>';
    }).join('') + '</div>';

    var bodyHtml = '<div class="ln-chip-fileview" id="ln-chip-fileview">'
      + '<div class="empty" style="padding:0.5rem">加载中…</div>'
      + '</div>';

    contentEl.innerHTML = listHtml + bodyHtml;

    // Wire file item clicks
    contentEl.querySelectorAll('[data-ln-file-rel]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        contentEl.querySelectorAll('.ln-chip-fileitem.active').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        _lnSetSetupPreviewAction(
          _lnSetupFileActionsHtml(btn.dataset.lnFileRel) + ' '
          + '<button class="btn-warning tiny" data-ln-preview-retry="' + phaseId + '">重新生成「' + escapeHtml(label) + '」</button> '
          + '<button class="ghost tiny" data-ln-preview-refresh="' + phaseId + '">↻ 刷新文件列表</button>'
        );
        _lnLoadChipFileInto(btn.dataset.lnFileRel, document.getElementById('ln-chip-fileview'));
      });
    });

    // Load default file
    _lnLoadChipFileInto(files[defaultIdx].path, document.getElementById('ln-chip-fileview'));

    // Bottom action bar
    var defaultPath = files[defaultIdx].path;
    _lnSetSetupPreviewAction(
      _lnSetupFileActionsHtml(defaultPath) + ' '
      + '<button class="btn-warning tiny" data-ln-preview-retry="' + phaseId + '">重新生成「' + escapeHtml(label) + '」</button> '
      + '<button class="ghost tiny" data-ln-preview-refresh="' + phaseId + '">↻ 刷新文件列表</button>'
    );
  }

  async function _lnLoadChipFileInto(relPath, container) {
    if (!container) return;
    container.classList.remove('is-editing');
    container.innerHTML = '<div class="empty" style="padding:0.5rem">加载中…</div>';
    try {
      var resp = await fetch('/api/long-novel/books/' + _lnActiveBookId + '/artifact?path=' + encodeURIComponent(relPath));
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      var data = await resp.json();
      var rawContent = data.content || '';
      var html = '<div class="ln-chip-filepath"><code>' + escapeHtml(relPath) + '</code></div>'
        + '<div class="ln-chip-file-rendered">' + renderMarkdown(rawContent.substring(0, 30000)) + '</div>';
      container.innerHTML = html;
      container.dataset.lnCurrentRelPath = relPath;
      container.dataset.lnCurrentContent = rawContent;
    } catch (err) {
      container.innerHTML = '<div class="empty">加载失败：' + escapeHtml(err.message || String(err)) + '</div>';
    }
  }

  function _lnEditChipFile(relPath, content, container) {
    container.classList.add('is-editing');
    container.innerHTML = '<div class="ln-artifact-edit-shell">'
      + '<div class="ln-artifact-edit-head">'
      + '<div><span>正在编辑</span><code>' + escapeHtml(relPath) + '</code></div>'
      + '<div class="ln-artifact-edit-actions">'
      + '<button class="btn-primary tiny" data-ln-file-save>保存</button>'
      + '<button class="ghost tiny" data-ln-file-cancel>取消</button>'
      + '</div></div>'
      + '<textarea class="ln-artifact-editor" spellcheck="false"></textarea>'
      + '</div>';
    var textarea = container.querySelector('.ln-artifact-editor');
    if (textarea) {
      textarea.value = content || '';
      textarea.selectionStart = 0;
      textarea.selectionEnd = 0;
      textarea.scrollTop = 0;
      setTimeout(function() {
        textarea.focus();
        textarea.selectionStart = 0;
        textarea.selectionEnd = 0;
        textarea.scrollTop = 0;
      }, 0);
    }
    var saveBtn = container.querySelector('[data-ln-file-save]');
    if (saveBtn) saveBtn.addEventListener('click', async function() {
      try {
        await api('/api/long-novel/books/' + _lnActiveBookId + '/artifact', {
          method: 'POST',
          body: { path: relPath, content: textarea ? textarea.value : '' }
        });
        toast('已保存：' + relPath, 'success');
        container.classList.remove('is-editing');
        _lnLoadChipFileInto(relPath, container);
      } catch (err) {
        toast('保存失败：' + err.message, 'error');
      }
    });
    var cancelBtn = container.querySelector('[data-ln-file-cancel]');
    if (cancelBtn) cancelBtn.addEventListener('click', function() {
      container.classList.remove('is-editing');
      _lnLoadChipFileInto(relPath, container);
    });
  }

  async function _lnStartEditFromCurrent(relPath, container) {
    var content = container.dataset.lnCurrentContent;
    if (typeof content !== 'string' || container.dataset.lnCurrentRelPath !== relPath) {
      try {
        var resp = await fetch('/api/long-novel/books/' + _lnActiveBookId + '/artifact?path=' + encodeURIComponent(relPath));
        var data = await resp.json();
        content = data.content || '';
      } catch (_e) {
        content = '';
      }
    }
    _lnEditChipFile(relPath, content, container);
  }

  async function _lnRegenerateArtifactFile(relPath, container) {
    var extra = _lnAskAdditionalPrompt(relPath);
    if (!await showConfirmAsync('确定重新生成当前文件？\n\n' + relPath + '\n\n现有内容会被覆盖。')) return;
    if (container) container.innerHTML = '<div class="empty" style="padding:0.5rem">正在重新生成…</div>';
    try {
      await api('/api/long-novel/books/' + _lnActiveBookId + '/artifact/regenerate', {
        method: 'POST',
        body: { path: relPath, additional_prompt: extra }
      });
      toast('已重新生成：' + relPath, 'success');
      _lnLoadChipFileInto(relPath, container || document.getElementById('ln-chip-fileview'));
    } catch (err) {
      toast('重新生成失败：' + err.message, 'error');
      if (container) container.innerHTML = '<div class="empty">重新生成失败：' + escapeHtml(err.message) + '</div>';
    }
  }
  function _lnSetOutlinePreviewAction(html) {
    var actionEl = document.getElementById('ln-outline-preview-actions');
    if (!actionEl) return;
    actionEl.innerHTML = html || '';
    var retryBtn = actionEl.querySelector('[data-ln-preview-retry]');
    if (retryBtn) retryBtn.addEventListener('click', function() { _lnRetryPhase(retryBtn.dataset.lnPreviewRetry); });
    var runBtn = actionEl.querySelector('[data-ln-preview-run]');
    if (runBtn) runBtn.addEventListener('click', function() { _lnRunSinglePhase(runBtn.dataset.lnPreviewRun); });
    var refreshBtn = actionEl.querySelector('[data-ln-outline-preview-refresh]');
    if (refreshBtn) refreshBtn.addEventListener('click', function() { _lnShowOutlineChipPreview(refreshBtn.dataset.lnOutlinePreviewRefresh); });
    var fileRegenBtn = actionEl.querySelector('[data-ln-preview-file-regen]');
    if (fileRegenBtn) fileRegenBtn.addEventListener('click', function() {
      _lnRegenerateArtifactFile(fileRegenBtn.dataset.lnPreviewFileRegen, document.getElementById('ln-outline-chip-fileview') || document.getElementById('ln-chip-fileview'));
    });
    var editBtn = actionEl.querySelector('[data-ln-preview-file-edit]');
    if (editBtn) editBtn.addEventListener('click', function() {
      var relPath = editBtn.dataset.lnPreviewFileEdit;
      var container = document.getElementById('ln-outline-chip-fileview') || document.getElementById('ln-chip-fileview');
      if (container) _lnStartEditFromCurrent(relPath, container);
    });
  }
  function _lnSetPhasePreviewAction(phaseId, html) {
    if (_lnIsOutlinePhase(phaseId)) _lnSetOutlinePreviewAction(html);
    else _lnSetSetupPreviewAction(html);
  }

  async function _lnRunSetupPhases() {
    var stripEl = document.getElementById('ln-setup-strip');
    var previewEl = document.getElementById('ln-setup-preview');
    var titleEl = document.getElementById('ln-setup-preview-title');
    var contentEl = document.getElementById('ln-setup-preview-content');
    _lnSetSetupPreviewAction('');

    // Build strip
    if (stripEl) {
      stripEl.style.display = 'flex';
      stripEl.innerHTML = _lnSetupPhases.map(function(ph) {
        return '<div class="phase-chip" data-ln-phase="' + ph.id + '" style="cursor:pointer" title="点击查看生成内容 | 失败时可重试">'
          + '<span class="phase-chip-icon"></span>'
          + '<span class="phase-chip-label">' + ph.icon + ' ' + ph.label + '</span>'
          + '<button class="phase-chip-prompt-btn" data-ln-prompt-view="' + ph.id + '" style="margin-left:0.3rem;cursor:pointer;font-size:0.7rem;padding:1px 4px;border-radius:3px;border:1px solid var(--border);background:transparent;color:var(--muted)" title="查看提示词"></button>'
          + '<span class="phase-chip-retry" data-ln-retry style="display:none;margin-left:0.5rem;cursor:pointer;font-size:0.75rem;padding:2px 6px;border-radius:3px;background:var(--danger);color:#fff">重试</span></div>';
      }).join('');
      _lnBindSetupStripActions(stripEl);
    }
    if (previewEl) previewEl.style.display = '';
    if (contentEl) contentEl.textContent = '准备开始...（每个步骤需要30-90秒，请耐心等待）';

    var allOk = true;
    for (var i = 0; i < _lnSetupPhases.length; i++) {
      var ok = await _lnRunOnePhase(_lnSetupPhases[i], stripEl, titleEl, contentEl);
      if (!ok) allOk = false;
      // Brief pause between phases
      await new Promise(function(r) { setTimeout(r, 600); });
    }

    if (!allOk) {
      if (contentEl) contentEl.textContent = '部分阶段失败，请点击 ✕ 标记的阶段上的「重试」按钮单独重试。';
      toast('设定未完成，请重试失败的步骤', 'warning');
      return;
    }

    if (titleEl) titleEl.textContent = '✓ 设定完成';
    if (contentEl) contentEl.textContent = '题材定位、世界观、角色设计已完成。请切换到「大纲」tab，继续生成大纲、卷纲和章节细纲。';
    toast('设定完成，请继续生成大纲', 'success');
  }

  async function _lnRunOnePhase(ph, stripEl, titleEl, contentEl, additionalPrompt) {
    _lnUpdateSetupChip(stripEl, ph.id, '');
    _lnSetPhasePreviewAction(ph.id, '');
    if (titleEl) titleEl.textContent = '' + ph.icon + ' ' + ph.label + ' — AI生成中...';
    if (contentEl) contentEl.textContent = '正在调用AI，请耐心等待（通常30-90秒）...';

    try {
      await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/' + ph.id, {
        method: 'POST',
        body: { additional_prompt: additionalPrompt || '' }
      });
    } catch (err) {
      _lnUpdateSetupChip(stripEl, ph.id, '✕');
      _lnShowRetryButton(stripEl, ph.id, true);
      if (contentEl) contentEl.textContent = '启动失败：' + (err.message || '未知错误') + ' — 请点击重试按钮';
      toast(ph.label + ' 启动失败', 'error');
      return false;
    }

    var startTime = Date.now();
    var done = false;
    while (!done) {
      await new Promise(function(r) { setTimeout(r, 2000); });
      try {
        var sData = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/' + ph.id + '/status');
        var st = sData.status || '?';
        var elapsed = Math.round((Date.now() - startTime) / 1000);
        if (contentEl) contentEl.textContent = '[' + (sData.updated_at || '') + ' 已等待' + elapsed + '秒] ' + (sData.detail || st);
        if (st === 'done') {
          done = true;
          _lnUpdateSetupChip(stripEl, ph.id, '✓');
          _lnMarkPhaseDone(stripEl, ph.id);
          _lnShowRetryButton(stripEl, ph.id, false);
          if (titleEl) titleEl.textContent = '✓ ' + ph.icon + ' ' + ph.label + ' — 完成';
          var preview = sData.detail || '';
          if (preview && preview.length > 20) {
            if (contentEl) contentEl.innerHTML = renderMarkdown(preview.substring(0, 5000));
          }
          return true;
        } else if (st === 'error') {
          done = true;
          _lnUpdateSetupChip(stripEl, ph.id, '✕');
          _lnShowRetryButton(stripEl, ph.id, true);
          if (titleEl) titleEl.textContent = '✕ ' + ph.label + ' — 失败';
          if (contentEl) contentEl.textContent = '错误：' + (sData.detail || '未知') + ' — 请点击重试按钮';
          toast(ph.label + ' 生成失败', 'error');
          return false;
        }
      } catch (_e) {
        if (contentEl) contentEl.textContent = '轮询出错，重试中...';
      }
    }
  }

  var _lnRetryInProgress = false;

  // Run a single pending/cancelled phase (not a retry of a failed one)
  async function _lnRunSinglePhase(phaseId) {
    if (_lnRetryInProgress) { toast('已有操作正在进行中，请等待完成', 'warning'); return; }
    var ph = _lnFindPhase(phaseId);
    if (!ph) return;

    _lnRetryInProgress = true;
    var useOutlineSurface = _lnIsOutlinePhase(phaseId);
    var stripEl = document.getElementById(useOutlineSurface ? 'ln-outline-phase-strip' : 'ln-setup-strip');
    var titleEl = document.getElementById(useOutlineSurface ? 'ln-outline-preview-title' : 'ln-setup-preview-title');
    var contentEl = document.getElementById(useOutlineSurface ? 'ln-outline-preview-content' : 'ln-setup-preview-content');

    // Ensure strip is built
    if (stripEl && !stripEl.querySelector('[data-ln-phase]')) {
      stripEl.style.display = 'flex';
      var phasesForStrip = useOutlineSurface ? _lnOutlinePhases : _lnSetupPhases;
      stripEl.innerHTML = phasesForStrip.map(function(p) {
        return '<div class="phase-chip" data-ln-phase="' + p.id + '" style="cursor:pointer">'
          + '<span class="phase-chip-icon"></span>'
          + '<span class="phase-chip-label">' + p.icon + ' ' + p.label + '</span>'
          + '<button class="phase-chip-prompt-btn" data-ln-prompt-view="' + p.id + '" style="margin-left:0.3rem;cursor:pointer;font-size:0.7rem;padding:1px 4px;border-radius:3px;border:1px solid var(--border);background:transparent;color:var(--muted)" title="查看提示词"></button>'
          + '<span class="phase-chip-retry" data-ln-retry style="display:none;margin-left:0.5rem;cursor:pointer;font-size:0.75rem;padding:2px 6px;border-radius:3px;background:var(--danger);color:#fff">重试</span></div>';
      }).join('');
      _lnBindSetupStripActions(stripEl);
    }

    _startGlobalProgressPolling();
    var previewPanel = document.getElementById(useOutlineSurface ? 'ln-outline-preview-content' : 'ln-setup-preview');
    if (previewPanel && !useOutlineSurface) previewPanel.style.display = '';
    var extraPrompt = _lnAskAdditionalPrompt(ph.label);
    var ok = await _lnRunOnePhase(ph, stripEl, titleEl, contentEl, extraPrompt);

    if (ok) {
      // Check if all are done now
      var allDone = await _lnAreAllPipelinePhasesDone();
      if (allDone) {
        try {
          if (contentEl) contentEl.textContent = '所有阶段完成，正在写入数据库...';
          await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/finalize', { method: 'POST', body: {} });
          var fDone = false;
          while (!fDone) {
            await new Promise(function(r) { setTimeout(r, 1500); });
            var fs = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/finalize/status');
            if (fs.status === 'done') { fDone = true; }
          }
          if (titleEl) titleEl.textContent = '✓ 开书设定全部完成！';
          if (contentEl) contentEl.textContent = '可以切换到「正文」标签开始逐章写作了。';
          toast('开书设定完成！', 'success');
          _stopGlobalProgressPolling();
          setTimeout(function() { loadSetupPanel(); loadOutlinePanel(); }, 1500);
        } catch (err) { if (contentEl) contentEl.textContent = '收尾失败：' + err.message; }
      } else {
        if (useOutlineSurface) loadOutlinePanel(); else loadSetupPanel();
      }
    } else {
      if (useOutlineSurface) loadOutlinePanel(); else loadSetupPanel();
    }
    await _lnRestoreAutopilotMonitor();
    _lnRetryInProgress = false;
  }

  async function _lnRetryPhase(phaseId) {
    if (_lnRetryInProgress) { toast('已有重试正在进行中，请等待完成', 'warning'); return; }
    var useOutlineSurface = _lnIsOutlinePhase(phaseId);
    var stripEl = document.getElementById(useOutlineSurface ? 'ln-outline-phase-strip' : 'ln-setup-strip');
    var titleEl = document.getElementById(useOutlineSurface ? 'ln-outline-preview-title' : 'ln-setup-preview-title');
    var contentEl = document.getElementById(useOutlineSurface ? 'ln-outline-preview-content' : 'ln-setup-preview-content');
    var ph = _lnFindPhase(phaseId);
    if (!ph) return;

    if (!await showConfirmAsync('确定重试「' + ph.label + '」？将重新调用AI生成。')) return;

    _lnRetryInProgress = true;
    _lnShowRetryButton(stripEl, phaseId, false);
    _lnSetPhasePreviewAction(phaseId, '');
    if (titleEl) titleEl.textContent = '' + ph.icon + ' ' + ph.label + ' — 重新生成中...';

    var extraPrompt = _lnAskAdditionalPrompt(ph.label);
    var ok = await _lnRunOnePhase(ph, stripEl, titleEl, contentEl, extraPrompt);

    if (ok) {
      // After successful retry, check if all previous phases are done, if so, maybe continue pipeline
      var allDone = await _lnAreAllPipelinePhasesDone();
      if (allDone) {
        // Run finalize
        try {
          if (contentEl) contentEl.textContent = '所有阶段完成，正在写入数据库...';
          await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/finalize', { method: 'POST', body: {} });
          var fDone = false;
          while (!fDone) {
            await new Promise(function(r) { setTimeout(r, 1500); });
            var fs = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/finalize/status');
            if (fs.status === 'done') { fDone = true; }
          }
          if (titleEl) titleEl.textContent = '✓ 开书设定全部完成！';
          if (contentEl) contentEl.textContent = '可以切换到「写作」标签开始逐章写作了。';
          toast('开书设定完成！', 'success');
          setTimeout(function() { loadWritingWorkbench(); loadBookOverview(); }, 1500);
        } catch (err) {
          if (contentEl) contentEl.textContent = '收尾失败：' + err.message;
        }
      } else {
        if (contentEl) contentEl.textContent += '\n\n还有未完成的阶段，请继续等待或手动触发。';
      }
    }
    await _lnRestoreAutopilotMonitor();
    _lnRetryInProgress = false;
  }

  function _lnShowRetryButton(stripEl, phaseId, show) {
    if (!stripEl) return;
    var chip = stripEl.querySelector('[data-ln-phase="' + phaseId + '"]');
    if (!chip) return;
    var btn = chip.querySelector('[data-ln-retry]');
    if (btn) btn.style.display = show ? '' : 'none';
  }

  function _lnMarkPhaseDone(stripEl, phaseId) {
    if (!stripEl) return;
    var chip = stripEl.querySelector('[data-ln-phase="' + phaseId + '"]');
    if (chip) chip.dataset.lnPhaseDone = 'true';
  }

  async function _lnAreAllPipelinePhasesDone() {
    if (!_lnActiveBookId) return false;
    var phases = _lnAllSetupPhases();
    for (var i = 0; i < phases.length; i++) {
      try {
        var sResp = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/' + phases[i].id + '/status');
        if ((sResp.status || 'pending') !== 'done') return false;
      } catch (_e) {
        return false;
      }
    }
    return true;
  }

  async function _lnLoadFile(relPath) {
    var previewEl = document.getElementById('ln-setup-preview');
    var titleEl = document.getElementById('ln-setup-preview-title');
    var contentEl = document.getElementById('ln-setup-preview-content');
    _lnSetPhasePreviewAction(phaseId, '');
    try {
      var resp = await fetch('/api/long-novel/books/' + _lnActiveBookId + '/artifact?path=' + encodeURIComponent(relPath));
      if (resp.ok) {
        var data = await resp.json();
        if (titleEl) titleEl.textContent = '' + relPath;
        if (contentEl) contentEl.innerHTML = renderMarkdown((data.content || '').substring(0, 15000));
      }
    } catch (_e) { if (contentEl) contentEl.textContent = '加载失败'; }
  }

  function _lnUpdateSetupChip(stripEl, phaseId, icon) {
    if (!stripEl) return;
    var chip = stripEl.querySelector('[data-ln-phase="' + phaseId + '"]');
    if (!chip) return;
    var iconEl = chip.querySelector('.phase-chip-icon');
    if (iconEl) iconEl.textContent = icon;
    if (icon === '') chip.style.outline = '2px solid var(--primary)';
    else if (icon === '✕') { chip.style.outline = '2px solid var(--danger)'; chip.dataset.lnPhaseDone = 'false'; }
    else chip.style.outline = '';
  }

  async function rewriteCurrentChapter() {
    if (!_lnActiveBookId || !_lnViewChapter) { toast('请先选择章节', 'error'); return; }
    if (!await showConfirmAsync('确定重写第' + _lnViewChapter + '章？将自动备份原稿并检查后续章节连续性。')) return;
    var release = withBusy($('ln-btn-rewrite'), '重写中…');
    try {
      var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/rewrite-chapter/' + _lnViewChapter, { method: 'POST' });
      toast('第' + _lnViewChapter + '章已重写' + (result.cascade_affected ? '，' + result.cascade_affected + '个后续章节可能需要检查' : ''), 'success');
      loadWritingWorkbench();
    } catch (err) { toast('重写失败：' + err.message, 'error'); }
    finally { release(); }
  }

  // ── Global progress polling ──
  var _lnProgressTimer = null;

  function _startGlobalProgressPolling() {
    if (_lnProgressTimer) clearInterval(_lnProgressTimer);
    _pollGlobalProgress();
    _lnProgressTimer = setInterval(_pollGlobalProgress, 2000);
  }

  function _stopGlobalProgressPolling() {
    if (_lnProgressTimer) { clearInterval(_lnProgressTimer); _lnProgressTimer = null; }
    var bar = $('ln-global-progress');
    if (bar) bar.style.display = 'none';
  }

  async function _pollGlobalProgress() {
    if (!_lnActiveBookId) { _stopGlobalProgressPolling(); return; }
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId + '/progress');
      var bar = $('ln-global-progress');
      var icon = $('ln-global-progress-icon');
      var text = $('ln-global-progress-text');
      var cancelBtn = $('ln-btn-cancel');
      if (!bar || !text) return;

      var active = data.active_phase;
      var phaseStatuses = data.phase_statuses || {};

      if (data.cancelled) {
        bar.style.display = 'flex';
        if (icon) icon.textContent = '';
        text.textContent = '已暂停 — 点击右侧按钮恢复';
        if (cancelBtn) { cancelBtn.textContent = '▶ 继续'; cancelBtn.className = 'btn-primary tiny'; cancelBtn.style.display = ''; }
        return;
      }

      if (active) {
        bar.style.display = 'flex';
        var labels = {premise:'题材定位', world:'世界观', characters:'角色设计', outline:'大纲', volume_outline:'卷纲', chapter_outlines:'章节细纲', extend_chapters:'追加章节'};
        var st = phaseStatuses[active] || {};
        if (icon) icon.textContent = '';
        text.textContent = '正在生成：' + (labels[active] || active) + ' — ' + (st.detail || '处理中...');
        if (cancelBtn) { cancelBtn.textContent = '暂停'; cancelBtn.className = 'btn-danger tiny'; cancelBtn.style.display = ''; }
      } else {
        bar.style.display = 'none';
      }
    } catch (_e) { /* silent */ }
  }

  // Cancel / Resume button
  var _cancelBtn = document.getElementById('ln-btn-cancel');
  if (_cancelBtn) {
    _cancelBtn.addEventListener('click', async function() {
      if (!_lnActiveBookId) return;
      var isPaused = _cancelBtn.textContent.indexOf('继续') >= 0;
      if (isPaused) {
        try {
          await api('/api/long-novel/books/' + _lnActiveBookId + '/resume', { method: 'POST' });
          toast('已恢复', 'success');
          _pollGlobalProgress();
        } catch (err) { toast('恢复失败：' + err.message, 'error'); }
      } else {
        if (!await showConfirmAsync('确定要暂停当前操作吗？\n\n已生成的步骤不会丢失，可在设定面板中重试。')) return;
        try {
          await api('/api/long-novel/books/' + _lnActiveBookId + '/cancel', { method: 'POST' });
          toast('已发送暂停信号，将在当前步骤完成后停止', 'warning');
          _pollGlobalProgress();
        } catch (err) { toast('暂停失败：' + err.message, 'error'); }
      }
    });
  }

  // ── Panel loaders ──

  async function loadSetupPanel() {
    if (!_lnActiveBookId) return;
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = data.book || {};
      var stripEl = document.getElementById('ln-setup-strip');
      var phases = _lnSetupPhases;
      var fileMap = {
        premise:['设定/题材定位.md','题材定位'],
        world:['设定/世界观/背景设定.md','世界观'],
        characters:['设定/角色/角色设定.md','角色设计']
      };

      if (stripEl) {
        stripEl.style.display = 'flex';
        stripEl.innerHTML = phases.map(function(ph) {
          return '<div class="phase-chip" data-ln-phase="' + ph.id + '" style="cursor:pointer" title="点击查看生成内容 | 失败时可重试">'
            + '<span class="phase-chip-icon"></span>'
            + '<span class="phase-chip-label">' + ph.icon + ' ' + ph.label + '</span>'
            + '<button class="phase-chip-prompt-btn" data-ln-prompt-view="' + ph.id + '" style="margin-left:0.3rem;cursor:pointer;font-size:0.7rem;padding:1px 4px;border-radius:3px;border:1px solid var(--border);background:transparent;color:var(--muted)" title="查看提示词"></button>'
            + '<span class="phase-chip-retry" data-ln-retry style="display:none;margin-left:0.5rem;cursor:pointer;font-size:0.75rem;padding:2px 6px;border-radius:3px;background:var(--danger);color:#fff">重试</span></div>';
        }).join('');
        _lnBindSetupStripActions(stripEl);
      }

      if (stripEl) {
        for (var i = 0; i < phases.length; i++) {
          var ph = phases[i];
          var st = 'pending';
          try { var sResp = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/' + ph.id + '/status'); st = sResp.status || 'pending'; } catch (_e) {}
          var icon = st === 'done' ? '✓' : st === 'error' ? '✕' : st === 'running' ? '' : '';
          var chip = stripEl.querySelector('[data-ln-phase="' + ph.id + '"]');
          if (chip) {
            var iconEl = chip.querySelector('.phase-chip-icon');
            if (iconEl) iconEl.textContent = icon;
            chip.dataset.lnPhaseDone = st === 'done' ? 'true' : 'false';
            chip.style.outline = st === 'error' ? '2px solid var(--danger)' : (st === 'running' ? '2px solid var(--primary)' : '');
            var rBtn = chip.querySelector('[data-ln-retry]');
            if (rBtn) rBtn.style.display = st === 'error' ? '' : 'none';
          }
        }
      }
    } catch (err) { toast('加载设定面板失败：' + err.message, 'error'); }
  }

  // ── Prompt modal ──
  var _promptModalPhase = '';
  var _promptModalPlaceholders = [];

  function _missingPromptPlaceholders(text, placeholders) {
    var content = String(text || '');
    return (placeholders || []).filter(function(p) {
      return content.indexOf('{' + p + '}') === -1;
    });
  }

  function _renderPromptPlaceholderHint(placeholders, editable) {
    var list = placeholders || [];
    if (!list.length) return editable ? '该模板没有登记变量。' : '该 user prompt 暂由源码动态拼装，当前仅可查看。';
    return '必须保留这些变量，保存时会检查：' + list.map(function(p) { return '{' + p + '}'; }).join(' · ')
      + '。它们运行时会自动替换成真实章节信息，不用手动改。';
  }
  async function _showPromptModal(phaseId) {
    var modal = document.getElementById('prompt-modal');
    var titleEl = document.getElementById('prompt-modal-title');
    var sysEl = document.getElementById('prompt-system-content');
    var userEl = document.getElementById('prompt-user-content');
    var hintEl = document.getElementById('prompt-placeholder-hint');
    if (!modal || !sysEl || !userEl) return;

    _promptModalPhase = phaseId;
    _promptModalPlaceholders = [];
    modal.style.display = 'flex';
    if (titleEl) titleEl.textContent = '提示词 — 加载中…';
    sysEl.value = '加载中…';
    userEl.value = '加载中…';
    sysEl.disabled = true;
    userEl.disabled = true;
    if (hintEl) hintEl.textContent = '';

    try {
      var data = await api('/api/long-novel/prompts/' + phaseId);
      if (titleEl) titleEl.textContent = '提示词 — ' + (data.label || phaseId);
      sysEl.value = data.system_prompt || '（未找到系统提示词文件）';
      userEl.value = data.user_template || '（无用户提示词模板）';
      sysEl.disabled = !data.editable_system;
      userEl.disabled = !data.editable_user;
      _promptModalPlaceholders = data.placeholders || [];
      if (hintEl) {
        var hintText = _renderPromptPlaceholderHint(_promptModalPlaceholders, data.editable_user);
        var related = data.related_prompts || [];
        hintEl.innerHTML = escapeHtml(hintText) + (related.length
          ? '<div class="prompt-related">相关子模板：' + related.map(function(id) {
              return '<button class="ghost tiny" data-related-prompt="' + escapeHtml(id) + '">' + escapeHtml(id) + '</button>';
            }).join(' ') + '</div>'
          : '');
        hintEl.querySelectorAll('[data-related-prompt]').forEach(function(btn) {
          btn.addEventListener('click', function() { _showPromptModal(btn.dataset.relatedPrompt || ''); });
        });
      }
    } catch (err) {
      sysEl.value = '加载失败：' + err.message;
      userEl.value = '加载失败';
    }
  }

  // Wire prompt modal close
  var promptModalClose = document.getElementById('prompt-modal-close');
  var promptModal = document.getElementById('prompt-modal');
  var promptModalSave = document.getElementById('prompt-modal-save');
  var promptModalRevert = document.getElementById('prompt-modal-revert');
  if (promptModalClose && promptModal) {
    promptModalClose.addEventListener('click', function() { promptModal.style.display = 'none'; });
    promptModal.addEventListener('click', function(e) { if (e.target === promptModal) promptModal.style.display = 'none'; });
  }
  if (promptModalSave) {
    promptModalSave.addEventListener('click', async function() {
      if (!_promptModalPhase) return;
      var sysEl = document.getElementById('prompt-system-content');
      var userEl = document.getElementById('prompt-user-content');
      var body = {};
      if (sysEl && !sysEl.disabled) body.system_prompt = sysEl.value || '';
      if (userEl && !userEl.disabled) body.user_template = userEl.value || '';
      if (!Object.keys(body).length) {
        toast('这个提示词暂时没有可保存的文件', 'warning');
        return;
      }
      if (userEl && !userEl.disabled) {
        var missing = _missingPromptPlaceholders(userEl.value || '', _promptModalPlaceholders);
        if (missing.length) {
          toast('不能保存：用户提示词缺少变量 ' + missing.map(function(p) { return '{' + p + '}'; }).join('、'), 'error');
          userEl.focus();
          return;
        }
      }
      try {
        var res = await api('/api/long-novel/prompts/' + encodeURIComponent(_promptModalPhase), { method: 'POST', body: body });
        toast(res.message || '提示词已保存', 'success');
      } catch (err) {
        toast('保存提示词失败：' + err.message, 'error');
      }
    });
  }
  if (promptModalRevert) {
    promptModalRevert.addEventListener('click', async function() {
      if (!_promptModalPhase) return;
      if (!window.confirm('恢复上一版提示词？当前编辑框里的未保存内容会被覆盖。')) return;
      try {
        var res = await api('/api/long-novel/prompts/' + encodeURIComponent(_promptModalPhase) + '/revert', { method: 'POST', body: {} });
        toast(res.message || '已恢复上一版提示词', 'success');
        _showPromptModal(_promptModalPhase);
      } catch (err) {
        toast('恢复提示词失败：' + err.message, 'error');
      }
    });
  }

  async function loadOutlinePanel() {
    if (!_lnActiveBookId) return;
    try {
      var stripEl = $('ln-outline-phase-strip');
      var previewTitle = $('ln-outline-preview-title');
      var previewContent = $('ln-outline-preview-content');
      _lnSetOutlinePreviewAction('');
      if (stripEl) {
        stripEl.style.display = 'flex';
        stripEl.innerHTML = _lnOutlinePhases.map(function(ph) {
          return '<div class="phase-chip" data-ln-phase="' + ph.id + '" style="cursor:pointer" title="点击查看生成内容 | 失败时可重试">'
            + '<span class="phase-chip-icon"></span>'
            + '<span class="phase-chip-label">' + ph.icon + ' ' + ph.label + '</span>'
            + '<button class="phase-chip-prompt-btn" data-ln-prompt-view="' + ph.id + '" style="margin-left:0.3rem;cursor:pointer;font-size:0.7rem;padding:1px 4px;border-radius:3px;border:1px solid var(--border);background:transparent;color:var(--muted)" title="查看提示词"></button>'
            + '<span class="phase-chip-retry" data-ln-retry style="display:none;margin-left:0.5rem;cursor:pointer;font-size:0.75rem;padding:2px 6px;border-radius:3px;background:var(--danger);color:#fff">重试</span></div>';
        }).join('');
        _lnBindSetupStripActions(stripEl);
        for (var i = 0; i < _lnOutlinePhases.length; i++) {
          var ph = _lnOutlinePhases[i];
          var st = 'pending';
          try { var sResp = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/' + ph.id + '/status'); st = sResp.status || 'pending'; } catch (_e) {}
          var icon = st === 'done' ? '✓' : st === 'error' ? '✕' : st === 'running' ? '' : '';
          var chip = stripEl.querySelector('[data-ln-phase="' + ph.id + '"]');
          if (chip) {
            var iconEl = chip.querySelector('.phase-chip-icon');
            if (iconEl) iconEl.textContent = icon;
            chip.dataset.lnPhaseDone = st === 'done' ? 'true' : 'false';
            chip.style.outline = st === 'error' ? '2px solid var(--danger)' : (st === 'running' ? '2px solid var(--primary)' : '');
            var rBtn = chip.querySelector('[data-ln-retry]');
            if (rBtn) rBtn.style.display = st === 'error' ? '' : 'none';
          }
        }
      }
      if (previewTitle) previewTitle.textContent = '选择上方阶段查看内容';
      if (previewContent) {
        previewContent.classList.add('empty');
        previewContent.textContent = '点击上方「大纲 / 卷纲 / 章节细纲」查看内容';
      }
    } catch (err) { toast('加载大纲面板失败：' + err.message, 'error'); }
  }

  var _lnExtendInProgress = false;

  async function _lnExtendChapters() {
    if (!_lnActiveBookId) { toast('请先选择一本书', 'error'); return; }
    if (_lnExtendInProgress) { toast('追加章节正在进行中，请稍等', 'warning'); return; }

    var btn = $('ln-btn-extend-chapters');
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = data.book || {};
      var chapters = book.chapters || [];
      var maxChapter = Number(book.target_chapters || 0);
      for (var i = 0; i < chapters.length; i++) {
        maxChapter = Math.max(maxChapter, Number(chapters[i].chapter_number || 0));
      }
      var suggested = maxChapter + 30;
      var input = window.prompt('追加到第几章？当前计划到第 ' + maxChapter + ' 章。', String(suggested));
      if (input === null) return;
      var newTarget = parseInt(input, 10);
      if (!newTarget || newTarget <= maxChapter) {
        toast('新总章数必须大于当前 ' + maxChapter + ' 章', 'warning');
        return;
      }
      if (newTarget > 2000) {
        toast('总章数不能超过 2000', 'warning');
        return;
      }
      var extra = window.prompt('本次续写提示词（可留空）。例如：进入第二卷，主角开始反攻，感情线升温但不摊牌。', '') || '';
      if (!await showConfirmAsync('确定追加第 ' + (maxChapter + 1) + ' 到第 ' + newTarget + ' 章吗？\n\n系统会生成续写规划和新增章节细纲，并把新增章节加入正文队列。已有正文和旧细纲不会重写。')) return;

      _lnExtendInProgress = true;
      var release = withBusy(btn, '追加中…');
      _startGlobalProgressPolling();
      await api('/api/long-novel/books/' + _lnActiveBookId + '/extend-chapters', {
        method: 'POST',
        body: { new_target_chapters: newTarget, additional_prompt: extra }
      });

      var done = false;
      var started = Date.now();
      while (!done) {
        await new Promise(function(r) { setTimeout(r, 2000); });
        var st = await api('/api/long-novel/books/' + _lnActiveBookId + '/extend-chapters/status');
        var status = st.status || 'pending';
        var elapsed = Math.round((Date.now() - started) / 1000);
        if (btn) btn.textContent = '追加中… ' + elapsed + 's';
        if (status === 'done') {
          done = true;
          toast(st.detail || ('已追加到第 ' + newTarget + ' 章'), 'success');
          loadOutlinePanel();
          loadWritingWorkbench();
          loadBookList();
          var previewTitle = $('ln-outline-preview-title');
          var previewContent = $('ln-outline-preview-content');
          if (previewTitle) previewTitle.textContent = '✓ 追加章节完成';
          if (previewContent) {
            previewContent.classList.remove('empty');
            previewContent.textContent = st.detail || ('已追加到第 ' + newTarget + ' 章');
          }
        } else if (status === 'error' || status === 'cancelled') {
          throw new Error(st.detail || '追加章节失败');
        }
      }
      release();
    } catch (err) {
      toast('追加章节失败：' + err.message, 'error');
      if (btn && btn.dataset.original) {
        btn.disabled = false;
        btn.textContent = btn.dataset.original;
        delete btn.dataset.original;
      }
    } finally {
      _lnExtendInProgress = false;
      _stopGlobalProgressPolling();
    }
  }

  function renderOutlineTree(outlineDir) {
    var files = (outlineDir.children || []).filter(function(c) { return !c.is_dir; }).sort(compareOutlineNodes);
    var bookFiles = files.filter(function(f) { return f.name === '大纲.md' || f.name.indexOf('全书') >= 0; });
    var volumeFiles = files.filter(function(f) { return f.name.indexOf('卷纲') >= 0; });
    var chapterFiles = files.filter(function(f) { return f.name.indexOf('细纲') >= 0; });
    var otherFiles = files.filter(function(f) {
      return bookFiles.indexOf(f) < 0 && volumeFiles.indexOf(f) < 0 && chapterFiles.indexOf(f) < 0;
    });
    var html = '<div class="ln-outline-tree">';
    html += '<details open><summary>大纲/</summary><div class="ln-outline-tree-children">';
    html += renderOutlineGroup('全书级结构', bookFiles, true);
    html += renderOutlineGroup('每卷', volumeFiles, false);
    html += renderChapterOutlineGroups(chapterFiles);
    if (otherFiles.length) html += renderOutlineGroup('其他文件', otherFiles, false);
    html += '</div></details></div>';
    return html;
  }

  function renderOutlineGroup(label, files, open) {
    if (!files || files.length === 0) return '';
    return '<details ' + (open ? 'open' : '') + '><summary>' + escapeHtml(label) + '</summary>'
      + '<div class="ln-outline-tree-children">'
      + files.map(renderOutlineFileButton).join('')
      + '</div></details>';
  }

  function renderChapterOutlineGroups(files) {
    if (!files || files.length === 0) return '';
    var html = '<details><summary>每章</summary><div class="ln-outline-tree-children">';
    var groups = {};
    files.forEach(function(file) {
      var n = outlineChapterNumber(file.name);
      var start = n ? (Math.floor((n - 1) / 20) * 20 + 1) : 0;
      var key = n ? (String(start).padStart(3, '0') + '-' + String(start + 19).padStart(3, '0')) : '其他';
      if (!groups[key]) groups[key] = [];
      groups[key].push(file);
    });
    Object.keys(groups).sort().forEach(function(key) {
      var label = key === '其他' ? '未编号细纲' : ('第 ' + key + ' 章');
      html += renderOutlineGroup(label, groups[key].sort(compareOutlineNodes), false);
    });
    html += '</div></details>';
    return html;
  }

  function renderOutlineFileButton(file) {
    var relPath = '大纲/' + file.name;
    var label = file.name.replace('细纲_第', '第').replace('章.md', '章').replace('.md', '');
    return '<button type="button" class="ln-outline-file" data-ln-outline-file="' + escapeHtml(relPath) + '">'
      + '<span></span><span>' + escapeHtml(label) + '</span>'
      + '<span class="ln-outline-file-size">' + escapeHtml(formatFileSize(file.size)) + '</span>'
      + '</button>';
  }

  function compareOutlineNodes(a, b) {
    var an = outlineChapterNumber(a.name);
    var bn = outlineChapterNumber(b.name);
    if (an && bn) return an - bn;
    if (an) return 1;
    if (bn) return -1;
    return String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN', { numeric: true });
  }

  function outlineChapterNumber(name) {
    var m = String(name || '').match(/第(\d+)章/);
    return m ? Number(m[1]) : 0;
  }

  async function _lnLoadOutlineFile(relPath) {
    var isOutline = String(relPath || '').indexOf('大纲/') === 0;
    var titleEl = isOutline ? document.getElementById('ln-outline-preview-title') : document.getElementById('ln-setup-preview-title');
    var contentEl = isOutline ? document.getElementById('ln-outline-preview-content') : document.getElementById('ln-setup-preview-content');
    var previewEl = isOutline ? null : document.getElementById('ln-setup-preview');
    if (isOutline) _lnSetOutlinePreviewAction('');
    if (previewEl) previewEl.style.display = '';
    if (titleEl) titleEl.textContent = '' + relPath;
    if (contentEl) {
      contentEl.classList.remove('empty');
      contentEl.textContent = '加载中…';
    }
    try {
      var resp = await fetch('/api/long-novel/books/' + _lnActiveBookId + '/artifact?path=' + encodeURIComponent(relPath));
      if (resp.ok) {
        var data = await resp.json();
        if (contentEl) contentEl.innerHTML = renderMarkdown((data.content||'').substring(0, 15000));
      }
    } catch (_e) { if (contentEl) contentEl.textContent = '加载失败'; }
  }

  async function loadBenchmarkPanel() {
    if (!_lnActiveBookId) return;
    try {
      var html = '';
      try {
        var resp = await fetch('/api/long-novel/books/' + _lnActiveBookId + '/artifact?path=' + encodeURIComponent('对标'));
        if (resp.ok) {
          var data = await resp.json();
          if (data.is_dir && data.files && data.files.length > 0) {
            html = data.files.map(function(f){return '<div style="cursor:pointer;color:var(--primary);text-decoration:underline;margin-bottom:0.3rem" data-ln-bench-file="'+escapeHtml('对标/'+f.name)+'">'+escapeHtml(f.name)+' ('+f.size+' bytes)</div>';}).join('');
          } else if (data.content) {
            html = renderMarkdown(data.content.substring(0, 15000));
          }
        }
      } catch (_e) {}
      $('ln-benchmark-content').innerHTML = html || '<span class="inbox-meta">暂无对标分析。创建书籍时可导入对标作品进行拆解。</span>';
      var benchEl = document.getElementById('ln-benchmark-content');
      if (benchEl) benchEl.addEventListener('click', function(e) {
        var el = e.target.closest('[data-ln-bench-file]');
        if (el) _lnLoadOutlineFile(el.dataset.lnBenchFile);
      });
    } catch (err) { toast('加载对标面板失败：' + err.message, 'error'); }
  }

  async function loadTrackingPanel() {
    if (!_lnActiveBookId) return;
    var grid = $('ln-tracking-cards');
    if (!grid) return;
    grid.innerHTML = '<div class="empty">加载中…</div>';
    var files = [
      { icon: '', label: '全书进展', path: '追踪/全书进展.md' },
      { icon: '', label: '角色状态', path: '追踪/角色状态.md' },
      { icon: '', label: '伏笔', path: '追踪/伏笔.md' },
      { icon: '', label: '时间线', path: '追踪/时间线.md' },
      { icon: '', label: '续写约束', path: '追踪/续写约束.md' },
      { icon: '', label: '写作上下文', path: '追踪/上下文.md' }
    ];
    try {
      await api('/api/long-novel/books/' + _lnActiveBookId + '/tracking/ensure', { method: 'POST' });
    } catch (_e) {}

    var cards = [];
    var entries = [];
    for (var i = 0; i < files.length; i++) {
      var item = files[i];
      var content = '';
      var size = 0;
      try {
        var resp = await fetch('/api/long-novel/books/' + _lnActiveBookId + '/artifact?path=' + encodeURIComponent(item.path));
        if (resp.ok) {
          var data = await resp.json();
          content = data.content || '';
          size = data.size || content.length || 0;
        }
      } catch (_e) {
        content = '';
      }
      var body = content.trim()
        ? renderMarkdown(content.substring(0, 30000))
        : '暂无内容';
      var bodyClass = content.trim() ? 'ln-tracking-body' : 'ln-tracking-body empty';
      entries.push({
        icon: item.icon,
        label: item.label,
        path: item.path,
        size: size,
        body: body,
        bodyClass: bodyClass
      });
      cards.push(
        '<button type="button" class="ln-tracking-card" data-ln-tracking-index="' + i + '">'
        + '<span class="ln-tracking-icon">' + item.icon + '</span>'
        + '<span class="ln-tracking-title"><strong>' + escapeHtml(item.label) + '</strong></span>'
        + '</button>'
      );
    }
    grid.innerHTML = cards.join('') + '<section id="ln-tracking-detail" class="ln-tracking-detail" hidden></section>';
    var detail = $('ln-tracking-detail');
    var activeIndex = -1;
    function closeTrackingDetail() {
      activeIndex = -1;
      grid.querySelectorAll('.ln-tracking-card').forEach(function(card) {
        card.classList.remove('is-active');
      });
      if (detail) {
        detail.hidden = true;
        detail.innerHTML = '';
      }
    }
    function openTrackingDetail(index) {
      var entry = entries[index];
      if (!entry || !detail) return;
      activeIndex = index;
      grid.querySelectorAll('.ln-tracking-card').forEach(function(card) {
        card.classList.toggle('is-active', Number(card.dataset.lnTrackingIndex) === index);
      });
      detail.hidden = false;
      detail.innerHTML = '<div class="ln-tracking-detail-head">'
        + '<div><strong>' + entry.icon + ' ' + escapeHtml(entry.label) + '</strong><code>' + escapeHtml(entry.path) + '</code></div>'
        + '<span class="ln-tracking-meta">' + escapeHtml(formatFileSize(entry.size)) + '</span>'
        + '<button type="button" class="ghost tiny" id="ln-tracking-detail-close">收起</button>'
        + '</div>'
        + '<div class="' + entry.bodyClass + '">' + entry.body + '</div>';
      var closeBtn = $('ln-tracking-detail-close');
      if (closeBtn) closeBtn.addEventListener('click', closeTrackingDetail);
    }
    grid.querySelectorAll('.ln-tracking-card').forEach(function(card) {
      card.addEventListener('click', function() {
        var index = Number(card.dataset.lnTrackingIndex);
        if (activeIndex === index) closeTrackingDetail();
        else openTrackingDetail(index);
      });
    });
  }

  // Old overview — kept for backward compat, redirects to setup
  async function loadBookOverview() { loadSetupPanel(); }

  // ── 资料/文件浏览器 ──



  async function loadBookFiles() {

    if (!_lnActiveBookId) {

      $('ln-file-tree').innerHTML = '<span class="inbox-meta">请先选择一本书</span>';

      return;

    }

    try {

      $('ln-file-tree').innerHTML = '<span class="inbox-meta">加载中…</span>';

      var data = await api('/api/long-novel/books/' + _lnActiveBookId + '/tree');

      var tree = data.tree;

      if (!tree || !tree.children || tree.children.length === 0) {

        $('ln-file-tree').innerHTML = '<span class="inbox-meta">暂无文件</span>';

        return;

      }

      $('ln-file-tree').innerHTML = renderFileTree(tree.children, 0);

    } catch (err) {

      $('ln-file-tree').innerHTML = '<span class="inbox-meta">加载失败：' + escapeHtml(err.message) + '</span>';

    }

  }



  function renderFileTree(children, depth) {

    var html = '<ul style="list-style:none;padding-left:' + (depth > 0 ? '1rem' : '0') + ';margin:0">';

    children.forEach(function(node) {

      if (node.is_dir) {

        var icons = {

          '设定': '', '世界观': '', '角色': '', '势力': '',

          '大纲': '', '正文': '', '追踪': '', '对标': '',

          '原文': '', '剧情': '',

        };

        var icon = icons[node.name] || '';

        html += '<li style="margin:2px 0">';

        html += '<div class="ln-tree-folder" style="cursor:pointer;padding:2px 4px;border-radius:3px;display:flex;align-items:center;gap:4px" onclick="var el=this.parentElement.querySelector(\'ul\'); if(el){ el.style.display=el.style.display===\'none\'?\'\':\'none\'; this.querySelector(\'.ln-tree-arrow\').textContent=el.style.display===\'none\'?\'\u25b6\':\'\u25bc\'; }">';

        html += '<span class="ln-tree-arrow" style="width:16px;text-align:center;font-size:0.7rem">▼</span>';

        html += '<span>' + icon + '</span>';

        html += '<span style="font-size:0.82rem;font-weight:600">' + escapeHtml(node.name) + '</span>';

        html += '</div>';

        if (node.children && node.children.length > 0) {

          html += renderFileTree(node.children, depth + 1);

        }

        html += '</li>';

      } else {

        html += '<li style="margin:1px 0;padding-left:' + (20 + depth * 12) + 'px;cursor:pointer;padding:2px 4px;border-radius:3px" onclick="_lnPreviewFile(\'' + escapeHtml(node.path) + '\', \'' + escapeHtml(node.name) + '\'); this.style.background=\'var(--primary-soft)\'; var sibs=this.parentElement.querySelectorAll(\'li\'); sibs.forEach(function(s){ if(s!==this) s.style.background=\'\'; }.bind(this));">';

        html += '<span style="font-size:0.78rem">' + escapeHtml(node.name) + '</span>';

        html += '<span class="inbox-meta" style="margin-left:0.5rem;font-size:0.68rem">' + formatFileSize(node.size) + '</span>';

        html += '</li>';

      }

    });

    html += '</ul>';

    return html;

  }



  function renderMarkdown(text) {
    var html = escapeHtml(text);
    html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
    html = html.replace(/^---$/gm, '<hr>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/`(.+?)`/g, '<code>$1</code>');
    html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
    html = html.replace(/<\/blockquote>\n<blockquote>/g, '<br>');
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>[\s\S]*?<\/li>)/g, function(m) { return '<ul>' + m + '</ul>'; });
    html = html.replace(/<\/ul>\s*<ul>/g, '');
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    html = html.replace(/\n\n+/g, '<br><br>');
    html = html.replace(/\n/g, '<br>');
    return html;
  }

  function formatFileSize(bytes) {

    if (!bytes || bytes === 0) return '';

    if (bytes < 1024) return bytes + ' B';

    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';

    return (bytes / 1048576).toFixed(1) + ' MB';

  }



  async function _lnPreviewFile(relPath, fileName) {

    $('ln-file-preview-title').textContent = '' + fileName;

    $('ln-file-preview-content').textContent = '加载中…';

    try {

      var resp = await fetch('/api/long-novel/books/' + _lnActiveBookId + '/artifact?path=' + encodeURIComponent(relPath));

      if (!resp.ok) { $('ln-file-preview-content').textContent = '加载失败'; return; }

      var data = await resp.json();

      if (data.content) {
        var maxShow = 50000;
        var content = data.content;
        var truncated = false;
        if (content.length > maxShow) {
          content = content.substring(0, maxShow);
          truncated = true;
        }
        var isMd = fileName.toLowerCase().endsWith('.md');
        if (isMd) {
          var rendered = renderMarkdown(content);
          if (truncated) rendered += '<br><br><span class="inbox-meta">… (已截断 ' + (data.content.length - maxShow) + ' 字符)</span>';
          $('ln-file-preview-content').innerHTML = '<div style="line-height:1.8">' + rendered + '</div>';
        } else {

          if (truncated) content += '\n\n... (已截断 ' + (data.content.length - maxShow) + ' 字符)';

          $('ln-file-preview-content').textContent = content;

        }

      } else {

        $('ln-file-preview-content').textContent = '(空文件)';

      }

    } catch (err) {

      $('ln-file-preview-content').textContent = '加载失败：' + err.message;

    }

  }



  // Make functions globally accessible for onclick handlers

  window._lnPreviewFile = _lnPreviewFile;

  window.renderFileTree = renderFileTree;

  window.formatFileSize = formatFileSize;





  // ---------- Init ----------

  // 题材库（独立功能模块）
  var _tpGenres = [];

  async function loadThemePoolPage() {
    var statusEl = document.getElementById("tp-status");
    if (statusEl) statusEl.textContent = " 加载中...";
    try {
      var statsData = await api("/api/themes/stats");
      var stats = statsData.stats || {};

      var s = stats;
      var typesHtml = (s.types || []).map(function(t) {
        return "<span>" + (t.target_type === "short" ? "短篇" : "长篇") + ": " + t.count + "</span>";
      }).join(" · ");
      var statsEl = document.getElementById("tp-stats");
      if (statsEl) statsEl.innerHTML =
        '<div class="status-card"><div class="status-icon-box" style="background:var(--primary-soft)"></div><div>' + (s.total || 0) + '</div><div class="inbox-meta">总题材</div></div>' +
        '<div class="status-card"><div class="status-icon-box" style="background:var(--success-soft)"></div><div>' + (s.unconsumed || 0) + '</div><div class="inbox-meta">未使用</div></div>' +
        '<div class="status-card"><div class="status-icon-box" style="background:var(--warning-soft)"></div><div>' + (s.sources || []).length + '</div><div class="inbox-meta">来源渠道</div></div>' +
        '<div class="status-card"><div class="status-icon-box" style="background:var(--danger-soft)"></div><div>' + typesHtml + '</div><div class="inbox-meta">类型分布</div></div>';

      // Sources
      var srcData = await api("/api/themes/trending/sources");
      var sources = srcData.sources || [];
      var sourceCounts = {};
      (s.sources || []).forEach(function(sc) { sourceCounts[sc.source] = sc.count; });
      var activeSources = sources.filter(function(src) {
        return Number(sourceCounts[src.id] || 0) > 0;
      });
      var srcEl = document.getElementById("tp-sources");
      if (srcEl) srcEl.innerHTML = activeSources.map(function(src) {
        var count = Number(sourceCounts[src.id] || 0);
        var lastFetch = "";
        (s.sources || []).forEach(function(sc) {
          if (sc.source === src.id && sc.last_fetch) lastFetch = " · 最后更新: " + sc.last_fetch;
        });
        return '<div class="tp-source-card">'
          + '<div class="tp-source-head"><strong>' + escapeHtml(((src.icon || '') + ' ' + src.name).trim()) + '</strong> <span class="badge-indigo">' + count + '条</span></div>'
          + '<div class="inbox-meta">' + escapeHtml(src.desc || '') + '</div>'
          + '<div class="inbox-meta">频率: ' + escapeHtml(src.frequency || '') + escapeHtml(lastFetch) + '</div>'
          + (src.url ? '<div class="inbox-meta tp-source-url">' + escapeHtml(src.url) + '</div>' : '')
          + '</div>';
      }).join('');

      // Keywords
      var kwData = await api("/api/themes/trending/fanqie-keywords");
      var keywords = kwData.keywords || [];
      var kwEl = document.getElementById("tp-keywords");
      if (kwEl) {
        if (keywords.length > 0) {
          var maxCount = keywords[0].count;
          kwEl.innerHTML = keywords.slice(0, 15).map(function(k) {
            var pct = Math.round(k.count / maxCount * 100);
            return '<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.2rem;font-size:0.82rem">'
              + '<span style="width:55px;text-align:right;font-weight:600">' + escapeHtml(k.keyword) + '</span>'
              + '<span style="flex:1;background:var(--panel-soft);border-radius:3px;height:6px;overflow:hidden">'
              + '<span style="background:var(--warning);height:100%;display:block;width:' + pct + '%"></span></span>'
              + '<span style="width:25px;font-size:0.7rem;color:var(--muted)">' + k.count + '</span></div>';
          }).join('');
        } else {
          kwEl.innerHTML = '<span class="inbox-meta">点击上方"拉取番茄榜单"获取实时数据</span>';
        }
      }
      var kwTimeEl = document.getElementById("tp-keywords-time");
      if (kwTimeEl) kwTimeEl.textContent = s.last_fanqie_fetch ? " (" + s.last_fanqie_fetch + ")" : "";

      // Genre filter
      _tpGenres = (s.genres || []).map(function(g) { return g.genre; });
      var genreSel = document.getElementById("tp-filter-genre");
      if (genreSel) {
        genreSel.innerHTML = '<option value="">全部分类</option>' + _tpGenres.map(function(g) {
          return '<option value="' + escapeHtml(g) + '">' + escapeHtml(g) + '</option>';
        }).join('');
      }

      // Category Trend Analysis
      try {
        var trendData = await api("/api/themes/trending/analysis");
        var cats = trendData.categories || [];
        var trendTimeEl = document.getElementById("tp-trend-time");
        if (trendTimeEl) trendTimeEl.textContent = trendData.total_categories ? "共" + trendData.total_categories + "个分类" : "";
        var trendListEl = document.getElementById("tp-trend-list");
        if (trendListEl && cats.length > 0) {
          var maxReads = cats[0].total_reads || 1;
          trendListEl.innerHTML = cats.map(function(c, i) {
            var barW = Math.round(c.hotness_score || 0);
            var medal = i === 0 ? '' : i === 1 ? '' : i === 2 ? '' : '';
            var hotLabel = c.hotness_score > 60 ? '' : c.hotness_score > 30 ? '' : '';
            var kws = (c.trending_keywords || []).slice(0, 5).map(function(k) {
              return '<span style="background:var(--warning-soft);padding:1px 6px;border-radius:3px;font-size:0.7rem">' + escapeHtml(k) + '</span>';
            }).join(' ');
            return '<div style="display:flex;align-items:center;gap:0.5rem;padding:0.35rem 0;border-bottom:1px solid var(--border);font-size:0.82rem">'
              + '<span style="width:24px;text-align:center">' + medal + '</span>'
              + '<span style="width:80px;font-weight:600">' + hotLabel + ' ' + escapeHtml(c.genre) + '</span>'
              + '<span style="flex:1;background:var(--panel-soft);border-radius:4px;height:10px;overflow:hidden">'
              + '<span style="background:var(--' + (i < 3 ? 'primary' : 'warning') + ');height:100%;display:block;width:' + barW + '%"></span></span>'
              + '<span style="width:70px;text-align:right;font-size:0.75rem;color:var(--muted)">' + (c.total_reads >= 10000 ? (c.total_reads / 10000).toFixed(1) + '万' : c.total_reads.toLocaleString()) + '</span>'
              + '<span style="width:200px;font-size:0.7rem;color:var(--muted)">' + kws + '</span>'
              + '</div>';
          }).join('');
        }
      } catch (_ignored) {}

      await loadThemeList();
      if (statusEl) statusEl.textContent = "";
    } catch (err) {
      var st = document.getElementById("tp-status");
      if (st) st.textContent = " 加载失败: " + err.message;
    }
  }

  async function loadThemeList() {
    var type = (document.getElementById("tp-filter-type") || {}).value || "";
    var genre = (document.getElementById("tp-filter-genre") || {}).value || "";
    var source = (document.getElementById("tp-filter-source") || {}).value || "";
    var unusedOnly = (document.getElementById("tp-filter-unused") || {}).checked || false;
    var params = "limit=200";
    if (type) params += "&type=" + encodeURIComponent(type);
    if (genre) params += "&genre=" + encodeURIComponent(genre);
    if (source) params += "&source=" + encodeURIComponent(source);

    var data = await api("/api/themes?" + params);
    var themes = data.themes || [];
    if (unusedOnly) themes = themes.filter(function(t) { return !t.is_consumed; });

    var countEl = document.getElementById("tp-filter-count");
    if (countEl) countEl.textContent = themes.length + " 条";
    var list = document.getElementById("tp-theme-list");
    if (!list) return;
    if (themes.length === 0) {
      list.innerHTML = '<div class="inbox-meta">暂无题材，点击"导入所有渠道"加载数据</div>';
      return;
    }

    var sourceIcons = {seeds: "", fanqie: "", manual: "", history: ""};
    list.innerHTML = themes.map(function(t) {
      var typeLabel = t.target_type === "long" ? "长篇" : "短篇";
      var consumedMark = t.is_consumed ? '<span class="tp-used-mark">已用</span>' : '';
      var srcIcon = sourceIcons[t.source] || "";
      var words = t.target_words_min ? (t.target_words_min + "-" + t.target_words_max + "字") : "";
      var timeInfo = t.fetched_at || t.created_at || "";
      if (timeInfo.length > 10) timeInfo = timeInfo.substring(0, 10);
      return '<div class="card-glass" style="padding:0.6rem 0.8rem;margin-bottom:0.3rem;font-size:0.85rem;cursor:pointer" data-tp-id="' + t.id + '">'
        + '<div style="display:flex;align-items:center;gap:0.5rem">'
        + consumedMark
        + '<span style="flex:1"><strong>' + escapeHtml((t.theme || "").substring(0, 80)) + '</strong></span>'
        + '<span class="badge-indigo">' + escapeHtml(t.genre || "") + '</span>'
        + '<span class="inbox-meta">' + escapeHtml(t.emotion || "") + '</span>'
        + '<span class="inbox-meta">' + srcIcon + '</span>'
        + '</div>'
        + '<div class="inbox-meta" style="margin-top:0.2rem;display:flex;gap:0.75rem">'
        + '<span>' + escapeHtml((t.hint_title || "").substring(0, 60)) + '</span>'
        + '<span>' + typeLabel + '</span>'
        + '<span>' + escapeHtml(t.platform || "") + '</span>'
        + (words ? '<span>' + words + '</span>' : '')
        + '<span style="margin-left:auto">' + timeInfo + '</span>'
        + '</div>'
        + '</div>';
    }).join('');

    list.querySelectorAll("[data-tp-id]").forEach(function(card) {
      card.addEventListener("click", function() {
        showThemeDetail(parseInt(card.dataset.tpId));
      });
    });
  }

  async function showThemeDetail(themeId) {
    try {
      var data = await api("/api/themes/" + themeId);
      var t = data.theme || {};
      var sourceIcons = {seeds: "演化", fanqie: "番茄榜单", manual: "手动", history: "历史"};
      var info =
        "ID: " + t.id + "\n" +
        "题材: " + (t.theme || "") + "\n" +
        "类型: " + (t.genre || "") + "\n" +
        "情绪: " + (t.emotion || "") + "\n" +
        "平台: " + (t.platform || "") + "\n" +
        "目标: " + (t.target_type === "long" ? "长篇" : "短篇") + "\n" +
        "标题参考: " + (t.hint_title || "") + "\n" +
        "字数: " + (t.target_words_min || "?") + "-" + (t.target_words_max || "?") + "\n" +
        "章数: " + (t.target_chapters || "N/A") + "\n" +
        "受众: " + (t.audience || "") + "\n" +
        "来源: " + (sourceIcons[t.source] || t.source) + "\n" +
        "来源详情: " + (t.source_detail || "") + "\n" +
        "获取时间: " + (t.fetched_at || t.created_at || "") + "\n" +
        "是否已用: " + (t.is_consumed ? "是" : "否") + "\n" +
        "AI评分: " + (t.ai_score || "N/A") + "\n" +
        (t.source_url ? "来源URL: " + t.source_url + "\n" : "");
      alert(info);
    } catch (err) {
      toast("加载详情失败: " + err.message, "error");
    }
  }

  function bindThemePoolPage() {
    var importBtn = document.getElementById("tp-btn-import-all");
    if (importBtn) importBtn.addEventListener("click", async function() {
      var st = document.getElementById("tp-status");
      if (st) st.textContent = " 导入中...";
      try {
        var res = await api("/api/themes/import-all", { method: "POST" });
        if (st) st.textContent = " 导入完成: " + res.result.total + "条";
        loadThemePoolPage();
      } catch (err) {
        if (st) st.textContent = " 失败: " + err.message;
      }
    });

    var fetchBtn = document.getElementById("tp-btn-fetch-fanqie");
    if (fetchBtn) fetchBtn.addEventListener("click", async function() {
      var st = document.getElementById("tp-status");
      if (st) st.textContent = " 拉取中...";
      try {
        var res = await api("/api/themes/import-fanqie", { method: "POST", body: {} });
        if (res.ok) {
          if (st) st.textContent = " 导入" + res.imported + "条 (日期:" + res.date + ")";
          loadThemePoolPage();
        } else {
          if (st) st.textContent = " 失败: " + (res.error || "未知错误");
        }
      } catch (err) {
        if (st) st.textContent = " 失败: " + err.message;
      }
    });

    ["tp-filter-type", "tp-filter-genre", "tp-filter-source"].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("change", loadThemeList);
    });
    var unusedCb = document.getElementById("tp-filter-unused");
    if (unusedCb) unusedCb.addEventListener("change", loadThemeList);
  }

  // ─────────────────────── 链路面板 (pipeline tab) ───────────────────────
  // Pipeline groups:
  //   setup    — 题材定位 → 世界观 → 角色设计 → 势力 → 关系     (一次性, 有 trace)
  //   outline  — 全书大纲 → 卷纲 → 章节细纲                       (一次性, 有 trace)
  //   chapter  — 初稿 → 扩写 → 润色 → 去AI → 连续性 → 定稿         (每章循环, 静态模板)
  var _lnPipelineSetupPhases = [
    {id: 'premise',     label: '题材定位', icon: ''},
    {id: 'world',       label: '世界观',   icon: ''},
    {id: 'characters',  label: '角色设计', icon: ''},
    {id: 'factions',    label: '势力',     icon: ''},
    {id: 'relations',   label: '关系',     icon: ''}
  ];
  var _lnPipelineOutlinePhases = [
    {id: 'outline',           label: '全书大纲',   icon: ''},
    {id: 'volume_outline',    label: '卷纲',       icon: ''},
    {id: 'chapter_outlines',  label: '章节细纲',   icon: ''}
  ];
  var _lnPipelineChapterSteps = [
    {id: 'draft',      label: '初稿',     icon: '', desc: '根据章纲、上下文、设定生成本章初稿。', source: 'l2_chapter_write.py:run_draft'},
    {id: 'expand',     label: '扩写',     icon: '', desc: '初稿未满3000字时调用；达到3000字会自动跳过，也可强制扩写。', source: 'l2_chapter_write.py:run_expand'},
    {id: 'polish',     label: '润色',     icon: '', desc: '精修语病、节奏、对话与画面感，不改变情节。', source: 'l2_chapter_write.py:run_polish'},
    {id: 'deslop',     label: '去AI味',   icon: '', desc: '删除 AI 高频词与工整句式，打散段落、口语化对话。', source: 'l2_chapter_write.py:run_deslop'},
    {id: 'review',     label: '审查',     icon: '', desc: '连续性检查（对比前文/设定/伏笔） + 故事架构 / 角色 / 文字 / 一致性 四维度自动审查，合并为审查报告。', source: 'l2_chapter_write.py:run_continuity_check + l4_review.py:run_full_review'},
    {id: 'finalize',   label: '定稿',     icon: '✓', desc: '写入正文目录，更新追踪记忆与续写约束。', source: 'l2_chapter_write.py:update_tracking_files'}
  ];
  var _lnPipelineState = {
    setup: {},     // id -> {status, has_trace, sub_trace_count, updated_at}
    activeId: null // currently selected chip
  };

  function _lnPipelineStatusBadge(status) {
    var map = {
      done:      {label: '✓ 完成', color: 'var(--success, #2da44e)'},
      running:   {label: '运行中', color: 'var(--primary, #0969da)'},
      starting:  {label: '启动中', color: 'var(--primary, #0969da)'},
      error:     {label: '✕ 失败', color: 'var(--danger, #cf222e)'},
      cancelled: {label: '已中断', color: 'var(--muted, #6e7781)'},
      pending:   {label: '未开始', color: 'var(--muted, #6e7781)'},
    };
    var info = map[status] || {label: status || '未知', color: 'var(--muted, #6e7781)'};
    return '<span class="ln-pl-badge" style="background:' + info.color + '">' + escapeHtml(info.label) + '</span>';
  }

  function _lnPipelineChipHtml(p, kind, status) {
    var statusCls = status ? (' status-' + status) : '';
    var dataKind = ' data-ln-pl-kind="' + kind + '"';
    var dataId = ' data-ln-pl-id="' + escapeHtml(p.id) + '"';
    return ''
      + '<button type="button" class="ln-pl-chip' + statusCls + '"' + dataKind + dataId + ' title="' + escapeHtml(p.label) + '">'
      +   '<span class="ln-pl-chip-icon">' + escapeHtml(p.icon || '') + '</span>'
      +   '<span>' + escapeHtml(p.label) + '</span>'
      +   '<span class="ln-pl-chip-dot"></span>'
      + '</button>';
  }

  function _lnRenderPipelineStrip() {
    var graphEl = document.getElementById('ln-pipeline-graph');
    if (!graphEl) return;
    graphEl.classList.remove('empty');
    var setupChips = _lnPipelineSetupPhases.map(function(p) {
      var st = (_lnPipelineState.setup[p.id] || {}).status || 'pending';
      return _lnPipelineChipHtml(p, 'setup', st);
    }).join('');
    var outlineChips = _lnPipelineOutlinePhases.map(function(p) {
      var st = (_lnPipelineState.setup[p.id] || {}).status || 'pending';
      return _lnPipelineChipHtml(p, 'setup', st);
    }).join('');
    var chapterChips = _lnPipelineChapterSteps.map(function(p) {
      return _lnPipelineChipHtml(p, 'chapter', null);
    }).join('');
    graphEl.innerHTML = ''
      + '<div class="ln-pipeline-strip">'
      +   '<div class="ln-pl-group">'
      +     '<div class="ln-pl-group-label">设定阶段</div>'
      +     '<div class="ln-pl-chips">' + setupChips + '</div>'
      +   '</div>'
      +   '<span class="ln-pl-arrow-sep">→</span>'
      +   '<div class="ln-pl-group">'
      +     '<div class="ln-pl-group-label">大纲阶段</div>'
      +     '<div class="ln-pl-chips">' + outlineChips + '</div>'
      +   '</div>'
      +   '<span class="ln-pl-arrow-sep">→</span>'
      +   '<div class="ln-pl-group loop">'
      +     '<span class="ln-pl-loop-badge">↻ 每章循环</span>'
      +     '<div class="ln-pl-group-label">正文阶段</div>'
      +     '<div class="ln-pl-chips">' + chapterChips + '</div>'
      +   '</div>'
      + '</div>';

    graphEl.querySelectorAll('.ln-pl-chip').forEach(function(chip) {
      chip.addEventListener('click', function() {
        _lnSetActiveChip(chip);
        var kind = chip.dataset.lnPlKind;
        var id = chip.dataset.lnPlId;
        if (kind === 'setup') {
          _lnShowSetupPhaseDetail(id);
        } else {
          _lnShowChapterStepDetail(id);
        }
      });
    });
  }

  function _lnSetActiveChip(chip) {
    document.querySelectorAll('#ln-pipeline-graph .ln-pl-chip.active').forEach(function(c) { c.classList.remove('active'); });
    if (chip) chip.classList.add('active');
  }

  function _lnFormatBytes(n) {
    if (n == null) return '?';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(2) + ' MB';
  }

  function _lnRenderInputsTable(inputs) {
    if (!inputs || !inputs.length) return '<div class="empty" style="padding:0.4rem">无输入元数据</div>';
    var rows = inputs.map(function(it) {
      if (it.kind === 'file') {
        var existIcon = it.exists ? '✓' : '⚠ 缺失';
        return '<tr>'
          + '<td>文件</td>'
          + '<td><code>' + escapeHtml(it.path || '') + '</code></td>'
          + '<td>' + escapeHtml(it.label || '') + '</td>'
          + '<td>' + _lnFormatBytes(it.bytes_used) + ' / ' + _lnFormatBytes(it.bytes_total) + '</td>'
          + '<td>' + existIcon + '</td>'
          + '</tr>';
      }
      return '<tr>'
        + '<td>参数</td>'
        + '<td><code>' + escapeHtml(it.name || '') + '</code></td>'
        + '<td colspan="2"><pre style="margin:0;white-space:pre-wrap;max-height:80px;overflow:auto">' + escapeHtml(String(it.value || '').slice(0, 500)) + '</pre></td>'
        + '<td>' + _lnFormatBytes(it.bytes_used) + '</td>'
        + '</tr>';
    });
    return '<table class="ln-pipeline-table">'
      + '<thead><tr><th>类型</th><th>路径/名</th><th>说明</th><th>用量/全量</th><th>状态</th></tr></thead>'
      + '<tbody>' + rows.join('') + '</tbody>'
      + '</table>';
  }

  function _lnPipelineFold(title, bodyHtml, options) {
    options = options || {};
    var cls = 'ln-pipeline-section ln-pl-fold' + (options.className ? ' ' + options.className : '');
    var meta = options.meta ? '<span class="ln-pl-fold-meta">' + escapeHtml(options.meta) + '</span>' : '';
    return '<details class="' + cls + '"' + (options.open ? ' open' : '') + '>'
      + '<summary><span>' + escapeHtml(title) + '</span>' + meta + '<b>展开</b></summary>'
      + '<div class="ln-pl-fold-body">' + (bodyHtml || '') + '</div>'
      + '</details>';
  }

  function _lnRenderTraceBlock(trace, fallbackPrompt) {
    if (!trace && !fallbackPrompt) {
      return '<div class="empty" style="padding:0.5rem">没有任何数据可显示</div>';
    }
    if (!trace) {
      return _lnPipelineFold(
        '提示词模板',
        '<div class="ln-pl-subhead">System prompt（' + escapeHtml(fallbackPrompt.label || '') + '）</div>'
          + '<pre class="ln-pl-pre">' + escapeHtml(fallbackPrompt.system_prompt || '（无）') + '</pre>'
          + '<div class="ln-pl-subhead">User template（变量未替换）</div>'
          + '<pre class="ln-pl-pre">' + escapeHtml(fallbackPrompt.user_template || '（无）') + '</pre>',
        { meta: 'trace 上线前完成，无真实运行数据' }
      );
    }

    var usage = trace.usage || {};
    var usageLine = (
      'input=' + (usage.input_tokens || 0)
      + ' · cached=' + (usage.cached_tokens || 0)
      + ' (' + Math.round((usage.cache_hit_ratio || 0) * 100) + '%)'
      + ' · output=' + (usage.output_tokens || 0)
    );

    var reasoningBlock = '';
    if (trace.reasoning) {
      reasoningBlock = ''
        + '<details style="margin-top:0.5rem">'
        +   '<summary style="cursor:pointer;color:var(--muted)">reasoning（点击展开）</summary>'
        +   '<pre class="ln-pl-pre">' + escapeHtml(trace.reasoning) + '</pre>'
        + '</details>';
    }

    var errorBlock = '';
    if (trace.error) {
      errorBlock = '<div style="padding:0.5rem;border-left:3px solid var(--danger);margin-bottom:0.5rem;color:var(--danger)">✕ 错误：' + escapeHtml(trace.error) + '</div>';
    }

    return ''
      + errorBlock
      + _lnPipelineFold('输入', _lnRenderInputsTable(trace.inputs), {
        meta: (trace.inputs || []).length ? (trace.inputs.length + ' 项') : '未记录'
      })
      + _lnPipelineFold('调用参数',
        '<div class="ln-pl-meta">'
          + '<span><b>model:</b> ' + escapeHtml(trace.model || '?') + '</span>'
          + '<span><b>thinking_mode:</b> ' + (trace.thinking_mode ? '✓' : '✕') + '</span>'
          + '<span><b>temperature:</b> ' + escapeHtml(String(trace.temperature || '?')) + '</span>'
          + '<span><b>finish_reason:</b> ' + escapeHtml(trace.finish_reason || '?') + '</span>'
          + '<span><b>cached:</b> ' + (trace.cached ? '✓' : '✕') + '</span>'
          + '<span><b>duration:</b> ' + escapeHtml(String(trace.duration_seconds || '?')) + 's</span>'
          + '<span><b>started:</b> ' + escapeHtml(trace.started_at || '') + '</span>'
        + '</div>'
      )
      + _lnPipelineFold('完整提示词',
        '<div class="ln-pl-subhead">System prompt</div>'
          + '<pre class="ln-pl-pre">' + escapeHtml(trace.system_prompt || '') + '</pre>'
          + '<div class="ln-pl-subhead">User prompt（变量已替换、上游已截断）</div>'
          + '<pre class="ln-pl-pre">' + escapeHtml(trace.user_prompt || '') + '</pre>',
        { meta: '真实运行时' }
      )
      + _lnPipelineFold('输出',
        '<div class="ln-pl-meta"><span><b>token usage:</b> ' + escapeHtml(usageLine) + '</span></div>'
          + '<div class="ln-pl-subhead">写入文件</div>'
          + '<div>' + (trace.outputs && trace.outputs.length ? trace.outputs.map(function(o){return '<code>' + escapeHtml(o) + '</code>';}).join(' · ') : '<span class="empty">（未记录）</span>') + '</div>'
          + '<div class="ln-pl-subhead">LLM 完整返回</div>'
          + '<pre class="ln-pl-pre">' + escapeHtml(trace.output_text || '') + '</pre>'
          + reasoningBlock,
        { meta: usageLine }
      );
  }

  function _lnPipelineDetailEl() { return document.getElementById('ln-pipeline-detail'); }

  function _lnPipelineFindSetupPhase(id) {
    var all = _lnPipelineSetupPhases.concat(_lnPipelineOutlinePhases);
    for (var i = 0; i < all.length; i++) { if (all[i].id === id) return all[i]; }
    return null;
  }

  async function _lnShowSetupPhaseDetail(phaseId) {
    var detail = _lnPipelineDetailEl();
    if (!detail) return;
    var phase = _lnPipelineFindSetupPhase(phaseId) || {id: phaseId, label: phaseId, icon: ''};
    var state = _lnPipelineState.setup[phaseId] || {};
    detail.classList.remove('empty');
    detail.innerHTML = ''
      + '<div class="ln-pipeline-detail-head">'
      +   '<h4>' + escapeHtml(phase.icon + ' ' + phase.label) + '</h4>'
      +   _lnPipelineStatusBadge(state.status || 'pending')
      +   (state.updated_at ? '<small style="color:var(--muted)">' + escapeHtml(state.updated_at) + '</small>' : '')
      + '</div>'
      + '<div class="empty" style="padding:0.5rem">加载中…</div>';
    if (!_lnActiveBookId) {
      detail.querySelector('.empty').textContent = '请先选择一本书';
      return;
    }

    var trace = null;
    var subTraces = [];
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/' + phaseId + '/trace');
      if (data.has_trace) {
        trace = data.trace;
        subTraces = data.sub_traces || [];
      }
    } catch (_e) {}

    var fallbackPrompt = null;
    if (!trace && !subTraces.length) {
      try { fallbackPrompt = await api('/api/long-novel/prompts/' + phaseId); } catch (_e) {}
    }

    var html = '';
    if (trace) html = _lnRenderTraceBlock(trace, null);
    else if (subTraces.length) html = '<div class="empty" style="padding:0.5rem;color:var(--muted)">主 trace 缺失，但找到 ' + subTraces.length + ' 个补齐批次 trace（见下方）</div>';
    else html = _lnRenderTraceBlock(null, fallbackPrompt);

    if (subTraces.length) {
      html += '<div class="ln-pipeline-section">'
        + '<h5>补齐批次 trace（' + subTraces.length + ' 个）</h5>';
      subTraces.forEach(function(st) {
        var d = st.data || {};
        html += '<details style="margin-bottom:0.5rem">'
          + '<summary style="cursor:pointer">' + escapeHtml(st.suffix || st.file) + ' · ' + escapeHtml(d.started_at || '') + ' · '
          + 'input=' + ((d.usage || {}).input_tokens || 0) + ' / output=' + ((d.usage || {}).output_tokens || 0) + '</summary>'
          + _lnRenderTraceBlock(d, null)
          + '</details>';
      });
      html += '</div>';
    }

    detail.innerHTML = ''
      + '<div class="ln-pipeline-detail-head">'
      +   '<h4>' + escapeHtml(phase.icon + ' ' + phase.label) + '</h4>'
      +   _lnPipelineStatusBadge(state.status || 'pending')
      +   (state.updated_at ? '<small style="color:var(--muted)">' + escapeHtml(state.updated_at) + '</small>' : '')
      +   '<button class="btn-primary tiny" data-ln-prompt-view="' + escapeHtml(phaseId) + '">编辑提示词</button>'
      + '</div>'
      + html;
    var promptBtn = detail.querySelector('[data-ln-prompt-view]');
    if (promptBtn) {
      promptBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        _showPromptModal(promptBtn.dataset.lnPromptView);
      });
    }
  }

  // 每个章节 step 的输入/输出文件清单（用于链路 tab 的可折叠展示）
  var _lnChapterStepIO = {
    draft: {
      inputs: [
        {label: 'system prompt 模板', path: 'generator/long_novel/prompts/l2_draft_system.txt', desc: '初稿角色设定提示词（可外部编辑，缺省时使用源码内置 fallback）'},
        {label: 'user prompt 模板', path: 'generator/long_novel/prompts/l2_draft_user.txt', desc: '初稿 user 模板；支持 {chapter_number}/{target_words}/{context_sections} 等变量'},
        {label: '章节细纲', path: '设定/章节细纲.md', desc: '当前章节的剧情大纲段落（含目标字数、节奏要求）'},
        {label: '上下文', path: '追踪/上下文.md', desc: '上一章末尾的剧情记忆 + 续写约束'},
        {label: '题材定位', path: '设定/题材定位.md', desc: '题材、风格、读者群体'},
        {label: '世界观', path: '设定/世界观/*.md', desc: '背景设定 / 力量体系 / 时代地理 / 历史大事件'},
        {label: '角色设计', path: '设定/角色设计.md', desc: '主要人物档案'},
        {label: '伏笔追踪', path: '追踪/伏笔.md', desc: '已埋未收的伏笔列表'},
      ],
      output: '正文/第NNN章/初稿.md',
      output_step: 'draft',
    },
    expand: {
      inputs: [
        {label: 'system prompt 模板', path: 'generator/long_novel/prompts/l2_expand_system.txt', desc: '扩写系统提示词，可在界面编辑'},
        {label: 'user prompt 模板', path: 'generator/long_novel/prompts/l2_expand_user.txt', desc: '扩写 user 模板；支持 {draft}/{current_words}/{target_words}/{shortfall}'},
        {label: '初稿', path: '正文/第NNN章/初稿.md', desc: '上一步生成的初稿（字数不足时调用）'},
        {label: '章节细纲', path: '设定/章节细纲.md', desc: '本章目标字数与节奏'},
      ],
      output: '正文/第NNN章/扩写.md',
      output_step: 'expand',
    },
    polish: {
      inputs: [
        {label: 'system prompt 模板', path: 'generator/long_novel/prompts/l2_polish_system.txt', desc: '润色系统提示词，可在界面编辑'},
        {label: 'user prompt 模板', path: 'generator/long_novel/prompts/l2_polish_user.txt', desc: '润色 user 模板；支持 {draft}'},
        {label: '扩写（或初稿）', path: '正文/第NNN章/扩写.md', desc: '语言润色对象'},
      ],
      output: '正文/第NNN章/润色.md',
      output_step: 'polish',
    },
    deslop: {
      inputs: [
        {label: 'system prompt 模板', path: 'generator/long_novel/prompts/l2_deslop_system.txt', desc: '去 AI 味的角色设定提示词（可外部编辑，缺省时使用源码内置 fallback）'},
        {label: '禁用词清单', path: 'l2_chapter_write.py:run_deslop banned_terms[]', desc: '内置 30+ 个 AI 高频词（仿佛/不禁/缓缓/眼中闪过…），实际命中的会注入 user prompt'},
        {label: 'user prompt 模板', path: 'generator/long_novel/prompts/l2_deslop_user.txt', desc: '去 AI user 模板；支持 {draft}/{hit_text}'},
        {label: '润色', path: '正文/第NNN章/润色.md', desc: '去 AI 味的处理对象（拼装到 user prompt 的 {draft} 占位符）'},
      ],
      output: '正文/第NNN章/去AI.md',
      output_step: 'deslop',
    },
    continuity: {
      inputs: [
        {label: '去 AI 后正文', path: '正文/第NNN章/去AI.md', desc: '检查对象'},
        {label: '上下文', path: '追踪/上下文.md', desc: '前文摘要'},
        {label: '伏笔', path: '追踪/伏笔.md', desc: '伏笔状态'},
        {label: '角色设计', path: '设定/角色设计.md', desc: '人设比对'},
      ],
      output: '（已并入 review 审查报告，不产生独立文件）',
      output_step: null,
    },
    finalize: {
      inputs: [
        {label: '去 AI 后正文（或润色）', path: '正文/第NNN章/去AI.md', desc: '最终采用版本'},
        {label: '审查报告', path: '正文/第NNN章/审查.json', desc: '通过则归档'},
      ],
      output: '正文/第NNN章/正文.md（章节最终成稿）+ 更新 追踪/上下文.md',
      output_step: 'finalize',
    },
    review: {
      inputs: [
        {label: '去 AI 后正文', path: '正文/第NNN章/去AI.md', desc: '审查对象（finalize 前的最后一份产物）'},
        {label: '上下文', path: '追踪/上下文.md', desc: '前文摘要 · 连续性检查使用'},
        {label: '伏笔', path: '追踪/伏笔.md', desc: '伏笔状态 · 连续性检查使用'},
        {label: '角色设计', path: '设定/角色设计.md', desc: '人设比对 · 连续性检查使用'},
        {label: '设定与世界观', path: '设定/* + 世界观/*', desc: '一致性维度的比对参考'},
      ],
      output: '正文/第NNN章/审查.json（连续性结果 + 故事架构 / 角色 / 文字 / 一致性 四维分数与详评）',
      output_step: 'review',
    },
  };

  function _lnRenderStaticInputsTable(inputs) {
    if (!inputs || !inputs.length) return '<div class="empty" style="padding:0.4rem">无输入元数据</div>';
    var rows = inputs.map(function(it) {
      return '<tr>'
        + '<td>文件</td>'
        + '<td><code>' + escapeHtml(it.path || '') + '</code></td>'
        + '<td>' + escapeHtml(it.label || '') + '</td>'
        + '<td>' + escapeHtml(it.desc || '') + '</td>'
        + '</tr>';
    });
    return '<table class="ln-pipeline-table">'
      + '<thead><tr><th>类型</th><th>路径</th><th>名称</th><th>说明</th></tr></thead>'
      + '<tbody>' + rows.join('') + '</tbody>'
      + '</table>';
  }

  function _lnShowChapterStepDetail(stepId) {
    var detail = _lnPipelineDetailEl();
    if (!detail) return;
    var step = null;
    for (var i = 0; i < _lnPipelineChapterSteps.length; i++) {
      if (_lnPipelineChapterSteps[i].id === stepId) { step = _lnPipelineChapterSteps[i]; break; }
    }
    if (!step) { detail.textContent = '未知步骤'; return; }

    var idx = _lnPipelineChapterSteps.indexOf(step);
    var nextStep = _lnPipelineChapterSteps[idx + 1];
    var nextHint = nextStep
      ? '<span><b>下一步：</b>' + escapeHtml(nextStep.icon + ' ' + nextStep.label) + '</span>'
      : '<span><b>下一步：</b>回到「初稿」继续下一章（循环）</span>';

    var io = _lnChapterStepIO[stepId] || {inputs: [], output: '（未登记）', output_step: null};

    detail.classList.remove('empty');
    detail.innerHTML = ''
      + '<div class="ln-pipeline-detail-head">'
      +   '<h4>' + escapeHtml(step.icon + ' ' + step.label) + '</h4>'
      +   '<span class="ln-pl-badge" style="background:var(--muted, #6e7781)">每章循环 · 静态模板</span>'
      + '</div>'
      + _lnPipelineFold('说明', '<div class="ln-pl-text">' + escapeHtml(step.desc) + '</div>')
      + _lnPipelineFold('流程信息',
        '<div class="ln-pl-meta">'
          + '<span><b>所属阶段：</b>每章正文（循环）</span>'
          + '<span><b>顺序：</b>第 ' + (idx + 1) + ' / ' + _lnPipelineChapterSteps.length + ' 步</span>'
          + nextHint
          + '<span><b>执行位置：</b><code>generator/long_novel/' + escapeHtml(step.source) + '</code><small style="margin-left:0.35rem;color:var(--muted,#9aa0a6)">这表示本步骤由哪个源码文件/函数负责，不是用户需要填写的内容。</small></span>'
        + '</div>'
      )
      + _lnPipelineFold('输入',
        '<div>' + _lnRenderStaticInputsTable(io.inputs) + '</div>'
          + '<div class="ln-pl-subhead">提示：以上为该步骤依赖的上游产物清单；具体每章传入的截断后内容受 token 预算控制，详情请见源代码。</div>',
        { meta: (io.inputs || []).length + ' 项', className: 'is-inputs' }
      )
      + _lnPipelineFold('输出',
        '<div class="ln-pl-meta"><span><b>产物路径：</b><code>' + escapeHtml(io.output) + '</code></span></div>'
          + '<div data-chstep-output style="margin-top:0.5rem"><div class="empty" style="padding:0.5rem">加载最近一次运行结果...</div></div>',
        { className: 'is-output' }
      )
      + _lnPipelineFold('提示词模板',
        '<div data-chstep-prompts><div class="empty" style="padding:0.5rem">加载提示词模板...</div></div>',
        { className: 'is-prompts' }
      );

    if (io.output_step) _lnLoadAllChaptersStepOutput(stepId, io.output_step);
    else {
      var holder = detail.querySelector('[data-chstep-output]');
      if (holder) holder.innerHTML = '<div class="empty" style="padding:0.5rem">该步骤不产生独立产物</div>';
    }
    _lnLoadChapterStepPrompt(stepId);
  }

  function _lnPromptVariableDescriptions(placeholders) {
    var desc = {
      chapter_number: '当前章节序号，例如 12',
      chapter_title: '当前章节标题',
      target_words: '本章目标字数',
      context_sections: '系统整理后的上下文材料，例如前情、设定、角色、伏笔等'
    };
    var list = placeholders || [];
    if (!list.length) return '';
    return '<div class="ln-pl-vars" style="margin:0.35rem 0 0.55rem;padding:0.45rem 0.6rem;border:1px solid rgba(255,255,255,0.12);border-radius:6px;background:rgba(255,255,255,0.03)">'
      + '<div style="font-weight:600;margin-bottom:0.25rem">模板变量说明</div>'
      + '<div style="color:var(--muted,#9aa0a6);font-size:0.8rem;margin-bottom:0.3rem">这些 <code>{变量名}</code> 会在运行时自动替换成真实内容，用户不用手动填写。</div>'
      + list.map(function(p) {
        return '<div style="display:flex;gap:0.45rem;align-items:flex-start;margin:0.16rem 0"><code>{' + escapeHtml(p) + '}</code><span>' + escapeHtml(desc[p] || '运行时自动填入的内容') + '</span></div>';
      }).join('')
      + '</div>';
  }

  function _lnRenderChapterPromptBlock(data) {
    if (!data || !data.ok) return '<div class="empty" style="padding:0.4rem">暂无提示词模板</div>';
    var vars = (data.placeholders || []).map(function(p) { return '<code>{' + escapeHtml(p) + '}</code>'; }).join(' · ');
    return ''
      + '<div class="ln-pl-meta" style="margin-bottom:0.5rem">'
      +   '<span><b>System 提示词文件：</b><code>' + escapeHtml(data.system_file || '无') + '</code></span>'
      +   '<span><b>User 提示词文件：</b><code>' + escapeHtml(data.user_file || '源码拼装') + '</code></span>'
      +   '<button class="btn-primary tiny" data-ln-prompt-view="' + escapeHtml(data.phase || '') + '">编辑提示词</button>'
      + '</div>'
      + (vars ? '<div class="ln-pl-subhead">模板里会出现的变量：' + vars + '</div>' : '')
      + _lnPromptVariableDescriptions(data.placeholders || [])
      + '<details class="ln-pl-nested-fold" style="margin-top:0.4rem"><summary>System prompt</summary>'
      +   '<pre class="ln-pl-pre">' + escapeHtml(data.system_prompt || '（无）') + '</pre>'
      + '</details>'
      + '<details class="ln-pl-nested-fold" style="margin-top:0.4rem"><summary>User prompt 模板</summary>'
      +   '<pre class="ln-pl-pre">' + escapeHtml(data.user_template || '（无）') + '</pre>'
      + '</details>';
  }

  async function _lnLoadChapterStepPrompt(stepId) {
    var detail = _lnPipelineDetailEl();
    if (!detail) return;
    var holder = detail.querySelector('[data-chstep-prompts]');
    if (!holder) return;
    try {
      var data = await api('/api/long-novel/prompts/' + encodeURIComponent(stepId));
      holder.innerHTML = _lnRenderChapterPromptBlock(data);
      holder.querySelectorAll('[data-ln-prompt-view]').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
          e.stopPropagation();
          _showPromptModal(btn.dataset.lnPromptView);
        });
      });
    } catch (err) {
      holder.innerHTML = '<div class="empty" style="padding:0.5rem">加载提示词失败：' + escapeHtml(err.message || String(err)) + '</div>';
    }
  }

  function _lnRenderChapterStepBody(outputStepName, data) {
    var d = data || {};
    if (outputStepName === 'review') {
      var review = d.review || {};
      var dims = review.dimensions || {};
      var overall = review.overall || '?';
      var dimRows = Object.keys(dims).map(function(k) {
        var v = dims[k] || {};
        return '<tr><td><code>' + escapeHtml(k) + '</code></td><td>' + escapeHtml(String(v.verdict || v.score || '')) + '</td><td>' + escapeHtml(String(v.comment || v.notes || '').slice(0, 400)) + '</td></tr>';
      }).join('');
      return '<div class="ln-pl-meta"><span><b>overall:</b> ' + escapeHtml(overall) + '</span></div>'
        + '<table class="ln-pipeline-table" style="margin-top:0.4rem"><thead><tr><th>维度</th><th>判定</th><th>评语</th></tr></thead><tbody>' + dimRows + '</tbody></table>';
    }
    if (outputStepName === 'expand' || outputStepName === 'polish' || outputStepName === 'deslop') {
      return _lnRenderDiffView(d.source_before || '', d.content || '', _lnDiffLabels(outputStepName));
    }
    var content = String(d.content || '');
    var words = d.word_count || d.final_words || 0;
    var preview = content.slice(0, 4000);
    var more = content.length > 4000 ? '\n\n... [已截断，共 ' + content.length + ' 字符]' : '';
    return '<div class="ln-pl-meta"><span><b>字数：</b>' + words + '</span><span><b>字节：</b>' + content.length + '</span></div>'
      + '<pre class="ln-pl-pre" style="max-height:360px;overflow:auto;margin-top:0.3rem">' + escapeHtml(preview + more) + '</pre>';
  }

  async function _lnLoadAllChaptersStepOutput(stepId, outputStepName) {
    var detail = _lnPipelineDetailEl();
    if (!detail) return;
    var holder = detail.querySelector('[data-chstep-output]');
    if (!holder) return;
    if (!_lnActiveBookId) {
      holder.innerHTML = '<div class="empty" style="padding:0.5rem">请先选择一本书</div>';
      return;
    }
    holder.innerHTML = '<div class="empty" style="padding:0.5rem">加载章节列表...</div>';
    try {
      var chData = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters');
      var chapters = (chData && chData.chapters) || [];
      var candidates = chapters.filter(function(c) {
        return c.status && c.status !== 'outline_only';
      }).sort(function(a, b) { return a.chapter_number - b.chapter_number; });
      if (!candidates.length) {
        holder.innerHTML = '<div class="empty" style="padding:0.5rem">尚未写过任何章节。请到「正文」tab 触发某一步骤后再回来查看。</div>';
        return;
      }
      var html = '<div class="ln-pl-subhead" style="margin-bottom:0.3rem">共 ' + candidates.length + ' 章可查看 · 点击章节折叠条按需加载内容</div>';
      html += '<div data-chstep-list>';
      candidates.forEach(function(c) {
        var statusBadge = {
          writing: '写作中', draft: '已草稿', reviewed: '✓ 已审查', published: '已成稿',
        }[c.status] || ('' + c.status);
        html += '<details class="ln-chstep-ch" data-ch-num="' + c.chapter_number + '" style="margin-bottom:0.3rem;border:1px solid rgba(255,255,255,0.12);border-radius:4px;background:rgba(255,255,255,0.03)">'
          + '<summary style="cursor:pointer;padding:0.4rem 0.6rem;display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;background:rgba(255,255,255,0.04);color:var(--text,inherit);border-radius:4px">'
          +   '<span style="font-weight:600;color:var(--text,inherit)">第 ' + c.chapter_number + ' 章</span>'
          +   '<span style="flex:1;min-width:120px;color:var(--text,inherit);opacity:0.92">' + escapeHtml(c.title || '未命名') + '</span>'
          +   '<span style="font-size:0.78rem;color:var(--muted,#9aa0a6)">' + escapeHtml(statusBadge) + '</span>'
          + '</summary>'
          + '<div data-ch-body style="padding:0.5rem"><div class="empty" style="padding:0.25rem">点击展开后加载</div></div>'
          + '</details>';
      });
      html += '</div>';
      holder.innerHTML = html;
      holder.querySelectorAll('details.ln-chstep-ch').forEach(function(det) {
        det.addEventListener('toggle', function() {
          if (!det.open) return;
          if (det.dataset.loaded === '1') return;
          var chNum = parseInt(det.getAttribute('data-ch-num'), 10);
          var body = det.querySelector('[data-ch-body]');
          if (!body) return;
          body.innerHTML = '<div class="empty" style="padding:0.25rem">加载中...</div>';
          api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum + '/step/' + outputStepName).then(function(resp) {
            body.innerHTML = _lnRenderChapterStepBody(outputStepName, resp);
            det.dataset.loaded = '1';
          }).catch(function(err) {
            var msg = (err && err.status === 404) ? '该章未运行过此步骤，暂无产物' : ('加载失败：' + (err.message || err));
            body.innerHTML = '<div class="empty" style="padding:0.25rem;color:var(--muted)">' + escapeHtml(msg) + '</div>';
            det.dataset.loaded = '1';
          });
        });
      });
    } catch (err) {
      holder.innerHTML = '<div class="empty" style="padding:0.5rem;color:var(--danger,#c00)">加载章节列表失败：' + escapeHtml(String(err.message || err)) + '</div>';
    }
  }

  async function loadPipelinePanel() {
    var graphEl = document.getElementById('ln-pipeline-graph');
    var detailEl = _lnPipelineDetailEl();
    if (graphEl) { graphEl.classList.add('empty'); graphEl.textContent = '加载中…'; }
    if (detailEl) { detailEl.classList.add('empty'); detailEl.textContent = '点击上方任一节点查看该步骤的详情'; }
    _lnPipelineState.setup = {};
    if (!_lnActiveBookId) {
      if (graphEl) { graphEl.classList.add('empty'); graphEl.textContent = '请先选择一本书'; }
      return;
    }
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-pipeline');
      (data.phases || []).forEach(function(p) {
        _lnPipelineState.setup[p.id] = {
          status: p.status,
          has_trace: p.has_trace,
          sub_trace_count: p.sub_trace_count,
          updated_at: p.updated_at,
        };
      });
      _lnRenderPipelineStrip();
    } catch (err) {
      if (graphEl) graphEl.textContent = '加载失败：' + (err.message || err);
      toast('加载链路失败：' + err.message, 'error');
    }
  }

  function _lnBindPipelineToolbar() {
    var refreshBtn = document.getElementById('ln-pipeline-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', loadPipelinePanel);
  }

  var _initDone = false;

  function init() {
    if (_initDone) return;
    _initDone = true;
    // Nav bindings (always)
    $$('#nav button[data-target]').forEach((btn) => btn.addEventListener('click', () => showSection(btn.dataset.target)));
    // Deferred bindings (fast, no API calls)
    bindArtifactModal();
    bindConsole();
    bindReview();
    bindInbox();
    bindLogs();
    bindMonitor();
    bindOverviewExtras();
    bindPromptsPanel();
    bindLongNovel();
    _lnBindPipelineToolbar();
    bindThemePoolPage();
    bindModeToggle();
    bindSettings();
    initTheme();
    initDisplaySettings();
    initSidebarCollapse();
    document.body.classList.toggle('monitor-dashboard-active', !!document.querySelector('#monitor.active'));
    // Restart service button
    var restartBtn = document.getElementById('btn-restart-service');
    if (restartBtn) {
      restartBtn.addEventListener('click', async function() {
        if (!await showConfirmAsync('确定要重启服务吗？\n\nWebUI 将短暂不可用，约 5-10 秒后恢复。')) return;
        restartBtn.disabled = true;
        restartBtn.textContent = '重启中…';
        try {
          await fetch('/api/control/restart', { method: 'POST' });
          toast('重启请求已发送，请稍候…', 'info');
          // Poll for service to come back
          var attempts = 0;
          var checkBack = setInterval(async function() {
            attempts++;
            try {
              var resp = await fetch('/api/health');
              if (resp.ok) {
                clearInterval(checkBack);
                toast('服务已恢复 ✓', 'success');
                restartBtn.disabled = false;
                restartBtn.textContent = '重启服务';
              }
            } catch(e) {
              // Still down, keep waiting
            }
            if (attempts > 30) {
              clearInterval(checkBack);
              toast('服务恢复超时，请手动刷新页面', 'error');
              restartBtn.disabled = false;
              restartBtn.textContent = '重启服务';
            }
          }, 2000);
        } catch(err) {
          toast('重启请求失败：' + err.message, 'error');
          restartBtn.disabled = false;
          restartBtn.textContent = '重启服务';
        }
      });
    }
    // Monitor is the default entry now.
    loadMonitor();
    startMonitorTimer();

    // Studio chrome: live clock + breadcrumb sync
    initStudioChrome();
  }

  // ============================================================
  //   Studio chrome (top bar): clock + breadcrumb sync
  // ============================================================
  function initStudioChrome() {
    var clock = document.getElementById('chrome-clock');
    if (clock) {
      var p = function(n) { return String(n).padStart(2, '0'); };
      var tick = function() {
        var d = new Date();
        clock.textContent = p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds()) + ' UTC+8';
      };
      tick();
      setInterval(tick, 1000);
    }

    var chromeMap = {
      'overview':      { label: '总览驾驶舱',   num: '00' },
      'monitor':       { label: '监控面板',     num: '01' },
      'generate':      { label: '短篇创作',     num: '03' },
      'long-novel':    { label: '长篇书库',     num: '04' },
      'theme-pool':    { label: '题材库',       num: '05' },
      'logs':          { label: '系统日志',     num: '06' },
      'settings-edit': { label: '设置',         num: '07' }
    };
    var crumbEl = document.getElementById('chrome-crumb');
    var numEl = document.getElementById('chrome-num');
    function syncCrumb(target) {
      var m = chromeMap[target];
      if (!m) return;
      if (crumbEl) crumbEl.textContent = m.label;
      if (numEl) numEl.textContent = m.num;
    }
    // Sync on every nav click (delegated)
    document.querySelectorAll('#nav button[data-target]').forEach(function(btn) {
      btn.addEventListener('click', function() { syncCrumb(btn.dataset.target); });
    });
    // Initial: pick current active section
    var active = document.querySelector('section.section.active');
    if (active) syncCrumb(active.id);
  }

  // ============================================================
  //   Monitor / long-novel visual refresh overrides
  // ============================================================
  function renderOpsLongNovel(longNovel) {
    const card = $('ops-long-novel');
    const stateEl = $('ops-long-state');
    const metaEl = $('ops-long-meta');
    const eyebrow = $('ops-long-eyebrow');
    if (!card || !stateEl) return;
    longNovel = longNovel || {};
    const stats = longNovelProgressStats(longNovel);
    const books = Number(longNovel.books_total || 0);
    const level = books === 0 ? 'warn' : (stats.writing > 0 ? 'ok' : 'warn');
    card.classList.remove('level-ok', 'level-warn', 'level-danger');
    card.classList.add('level-' + level);
    stateEl.textContent = books + ' 本书';
    if (eyebrow) eyebrow.textContent = stats.planned ? ('成稿 ' + stats.pct + '%') : '暂无章节';
    if (metaEl) {
      metaEl.innerHTML =
        '<div><div class="lm-label">章节</div><div class="lm-value">' + stats.done + ' / ' + stats.planned + '</div></div>' +
        '<div><div class="lm-label">写作中</div><div class="lm-value">' + stats.writing + '</div></div>' +
        '<div><div class="lm-label">字数</div><div class="lm-value">' + fmtNum(longNovel.words_total || 0) + '</div></div>';
    }
  }

  function renderMonitorKpis(mon) {
    const target = $('monitor-kpis');
    if (!target) return;
    const u = mon.usage || {};
    const d1 = u.d1 || {};
    const d7 = u.d7 || {};
    const ln = mon.long_novel || {};
    const stats = longNovelProgressStats(ln);
    const daily = mon.daily_usage || [];
    const sparkCost = daily.map((d) => d.cost || 0);
    const sparkTokens = daily.map((d) => d.tokens || 0);
    const sparkCalls = daily.map((d) => d.calls || 0);
    const errs = mon.recent_errors || [];
    const today = new Date();
    const errPerDay = [];
    for (let i = 13; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const tag = d.toISOString().slice(0, 10);
      errPerDay.push(errs.filter((e) => (e.occurred_at || '').slice(0, 10) === tag).length);
    }
    const successRate = (d7.calls > 0) ? (1 - (d7.failures / d7.calls)) * 100 : 100;
    const tiles = [
      { tone: 'primary', win: '长篇', label: '书籍', value: fmtNum(ln.books_total || 0), unit: '本', spark: [ln.books_total || 0], color: 'var(--primary)' },
      { tone: 'success', win: '长篇', label: '成稿进度', value: stats.done + '/' + stats.planned, unit: '章', spark: [stats.done, stats.planned], color: 'var(--success)' },
      { tone: 'info', win: '长篇', label: '累计字数', value: fmtNum(ln.words_total || 0), unit: '字', spark: [ln.words_total || 0], color: 'var(--info)' },
      { tone: 'primary', win: '24H', label: '调用次数', value: fmtNum(d1.calls), unit: '', spark: sparkCalls, color: 'var(--primary)' },
      { tone: 'info', win: '24H', label: 'Token', value: fmtNum(d1.total_tokens), unit: '', spark: sparkTokens, color: 'var(--info)' },
      { tone: 'success', win: '24H', label: '花费', value: fmtCurrency(d1.cost_cny), unit: '', spark: sparkCost, color: 'var(--success)' },
      { tone: (d1.failures > 0 ? 'danger' : 'success'), win: '24H', label: '失败', value: fmtNum(d1.failures), unit: '次', spark: errPerDay, color: 'var(--danger)' },
      { tone: (successRate >= 95 ? 'success' : (successRate >= 80 ? 'warn' : 'danger')), win: '7D', label: '成功率', value: successRate.toFixed(1) + '%', unit: '', spark: sparkCalls, color: 'var(--success)' },
    ];
    target.innerHTML = tiles.map((t) => (
      '<div class="kpi-tile tone-' + t.tone + '" tabindex="0">' +
        '<div class="kpi-eyebrow"><span>' + escapeHtml(t.label) + '</span><span class="kpi-window">' + escapeHtml(t.win) + '</span></div>' +
        '<div class="kpi-value">' + escapeHtml(t.value) + (t.unit ? '<span class="kpi-unit">' + escapeHtml(t.unit) + '</span>' : '') + '</div>' +
        '<div class="kpi-spark">' + sparklineSVG(t.spark.length ? t.spark : [0, 0], t.color, { height: 28 }) + '</div>' +
      '</div>'
    )).join('');
  }

  function renderMonitorLongNovel(longNovel) {
    const target = $('monitor-long-novel');
    const meta = $('monitor-long-meta');
    if (!target) return;
    longNovel = longNovel || {};
    const stats = longNovelProgressStats(longNovel);
    const books = Number(longNovel.books_total || 0);
    if (meta) meta.textContent = books + ' 本书 · ' + stats.done + '/' + stats.planned + ' 章';
    if (!books) {
      target.classList.add('empty');
      target.textContent = '暂无长篇小说数据';
      return;
    }
    target.classList.remove('empty');
    const statusText = Object.keys(longNovel.status || {}).map(function(k) {
      return k + ' ' + longNovel.status[k];
    }).join(' · ') || '暂无状态';
    const doneStyle = 'style="width:' + stats.donePct.toFixed(2) + '%"';
    const writingStyle = 'style="left:' + stats.donePct.toFixed(2) + '%;width:' + stats.writingPct.toFixed(2) + '%"';
    const recent = longNovel.recent || [];
    target.innerHTML =
      '<div class="ln-monitor-hero">' +
        '<div class="ln-monitor-hero-main">' +
          '<div class="lm-label">长篇完成度</div>' +
          '<div class="lm-hero-number">' + stats.done + '<small>/' + stats.planned + ' 章</small></div>' +
          '<div class="lm-progress" title="成稿 ' + stats.done + ' 章 · 写作中 ' + stats.writing + ' 章"><i ' + doneStyle + '></i><b ' + writingStyle + '></b></div>' +
          '<div class="lm-progress-meta"><span>成稿 ' + stats.pct + '%</span><span>写作中 ' + stats.writing + '</span><span>待推进 ' + stats.remaining + '</span></div>' +
        '</div>' +
        '<div class="ln-monitor-stat-grid">' +
          '<div><span>书籍</span><strong>' + books + '</strong></div>' +
          '<div><span>细纲</span><strong>' + stats.outlined + '</strong></div>' +
          '<div><span>累计字数</span><strong>' + fmtNum(longNovel.words_total || 0) + '</strong></div>' +
          '<div><span>状态</span><strong title="' + escapeHtml(statusText) + '">' + escapeHtml(statusText) + '</strong></div>' +
        '</div>' +
      '</div>' +
      '<div class="ln-monitor-recent">' + recent.map(function(b) {
        const bStats = longNovelProgressStats({
          chapters_done: b.chapters_done,
          chapters_writing: b.chapters_writing,
          chapters_outline: b.chapters_outline,
          chapters_total: b.chapters_total,
          chapters_planned: b.chapters_planned || b.target_chapters
        });
        return '<button type="button" class="ln-monitor-book" data-ln-monitor-book="' + escapeHtml(b.id) + '" tabindex="0">' +
          '<span class="lm-book-title">' + escapeHtml(b.title || '未命名') + '</span>' +
          '<span class="lm-book-progress"><i style="width:' + bStats.donePct.toFixed(2) + '%"></i></span>' +
          '<span class="lm-book-meta">' + bStats.done + '/' + bStats.planned + ' 章 · ' + fmtNum(b.words_total || 0) + '字</span>' +
        '</button>';
      }).join('') + '</div>';
    target.querySelectorAll('[data-ln-monitor-book]').forEach(function(btn) {
      btn.addEventListener('click', function() { showSection('long-novel'); });
    });
  }

  function renderMonitorEvents(events) {
    const target = $('monitor-events');
    const meta = $('monitor-events-meta');
    if (!target) return;
    events = (events || []).filter(function(ev) { return ev.kind !== 'publish'; });
    if (meta) meta.textContent = events.length + ' 条';
    if (!events.length) {
      target.classList.add('empty');
      target.textContent = '暂无事件记录';
      return;
    }
    target.classList.remove('empty');
    target.innerHTML = events.map(function(ev) {
      const tone = eventTone(ev.status);
      const kind = (ev.kind || 'event') + ' / ' + (ev.status || 'unknown');
      const idTag = ev.story_id != null ? '<span class="me-id">#' + escapeHtml(ev.story_id) + '</span>' : '';
      const msg = ev.message || kind;
      return '<div class="mon-event-row tone-' + tone + '" tabindex="0" title="' + escapeHtml(ev.occurred_at || '') + '">' +
        '<span class="mon-event-icon"></span>' +
        '<span class="mon-event-main">' +
          '<span class="mon-event-kind">' + escapeHtml(kind) + '</span>' +
          '<span class="mon-event-msg">' + idTag + escapeHtml(msg) + '</span>' +
        '</span>' +
        '<span class="mon-event-time">' + escapeHtml(relTime(ev.occurred_at)) + '</span>' +
      '</div>';
    }).join('');
  }

  function renderMonitorHeatmap(errors) {
    const target = $('monitor-errors');
    if (!target) return;
    const now = new Date();
    const grid = [];
    for (let r = 0; r < 7; r++) grid.push(new Array(24).fill(0));
    let maxCount = 0;
    let totalCount = 0;
    (errors || []).forEach(function(e) {
      const d = parseTs(e.occurred_at);
      if (!d) return;
      const diffDays = Math.floor((now - d) / (86400 * 1000));
      if (diffDays < 0 || diffDays >= 7) return;
      const hour = d.getHours();
      grid[diffDays][hour] += 1;
      totalCount += 1;
      maxCount = Math.max(maxCount, grid[diffDays][hour]);
    });
    target.classList.remove('empty');
    const heatLevel = function(v) {
      if (v === 0) return '';
      const p = maxCount ? v / maxCount : 0;
      if (p > 0.75) return 'heat-4';
      if (p > 0.5) return 'heat-3';
      if (p > 0.25) return 'heat-2';
      return 'heat-1';
    };
    const head = '<div class="mon-heat-row head"><span class="mon-heat-axis"></span>' +
      Array.from({ length: 24 }, function(_, h) {
        return '<span>' + (h % 6 === 0 ? h : '') + '</span>';
      }).join('') + '</div>';
    const rows = grid.map(function(row, r) {
      const day = new Date(now);
      day.setDate(day.getDate() - r);
      const lbl = r === 0 ? '今天' : (r === 1 ? '昨天' : String(day.getMonth() + 1) + '/' + day.getDate());
      return '<div class="mon-heat-row"><span class="mon-heat-axis">' + lbl + '</span>' +
        row.map(function(v, h) {
          return '<div class="mon-heat-cell ' + heatLevel(v) + '" title="' + lbl + ' ' + h + ':00 · 错误 ' + v + ' 次"><span>' + (v || '') + '</span></div>';
        }).join('') +
        '</div>';
    }).join('');
    const summary = '<div class="mon-heat-summary">' +
      '<div><span>最近 7 天错误</span><strong>' + totalCount + '</strong></div>' +
      '<div><span>最高小时</span><strong>' + maxCount + '</strong></div>' +
      '<div><span>覆盖窗口</span><strong>7×24</strong></div>' +
      '</div>';
    target.innerHTML = summary + '<div class="mon-heat-grid">' + head + rows + '</div>';
  }

  function renderMonitorCompass(mon, conc) {
    const target = $('monitor-health');
    if (!target) return;
    const h = mon.health || {};
    const ln = mon.long_novel || {};
    const stats = longNovelProgressStats(ln);
    const sch = mon.schedule || {};
    const cMax = (conc && conc.max_concurrent) || 0;
    const cUse = (conc && conc.in_use) || 0;
    const cells = [
      { label: '数据库', value: h.db_path ? '就绪' : '未知', sub: h.db_path || '-', tone: h.db_path ? 'ok' : 'warn' },
      { label: '模型', value: mon.model || '未配置', sub: mon.dry_run ? 'Dry-run 模式' : 'Live 模式', tone: mon.model ? 'ok' : 'warn' },
      { label: '调度器', value: sch.enabled ? '启用' : '手动', sub: sch.cron || sch.interval || '-', tone: sch.enabled ? 'ok' : 'warn' },
      { label: '长篇小说', value: (ln.books_total || 0) + ' 本', sub: stats.done + '/' + stats.planned + ' 章 · ' + fmtNum(ln.words_total || 0) + '字', tone: (ln.books_total || 0) > 0 ? 'ok' : 'warn' },
      { label: '并发槽', value: cMax > 0 ? (cUse + ' / ' + cMax) : '-', sub: cMax > 0 ? (cUse >= cMax ? '已满载' : '有空位') : '未读取', tone: cMax > 0 ? (cUse >= cMax ? 'warn' : 'ok') : 'warn' },
    ];
    target.classList.remove('empty');
    target.innerHTML = cells.map(function(c) {
      return '<div class="compass-cell tone-' + c.tone + '" tabindex="0">' +
        '<div class="compass-info">' +
          '<div class="compass-label">' + escapeHtml(c.label) + '</div>' +
          '<div class="compass-value" title="' + escapeHtml(c.value) + '">' + escapeHtml(c.value) + '</div>' +
          '<div class="compass-sub" title="' + escapeHtml(c.sub) + '">' + escapeHtml(c.sub) + '</div>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function renderMonitorKpis(mon) {
    const target = $('monitor-kpis');
    if (!target) return;
    mon = mon || {};
    const u = mon.usage || {};
    const d1 = u.d1 || {};
    const d7 = u.d7 || {};
    const d30 = u.d30 || {};
    const lim = mon.limits || {};
    const h = mon.health || {};
    const ln = mon.long_novel || {};
    const daily = mon.daily_usage || [];
    const costSpark = daily.map((d) => d.cost || 0);
    const tokenSpark = daily.map((d) => d.tokens || 0);
    const callSpark = daily.map((d) => d.calls || 0);
    const stats = longNovelProgressStats(ln);
    const successRate = d7.calls > 0 ? Math.max(0, (1 - ((d7.failures || 0) / d7.calls)) * 100) : 100;
    const errRate = d7.calls > 0 ? Math.max(0, ((d7.failures || 0) / d7.calls) * 100) : 0;
    const dbSize = formatFileSize(h.db_size_bytes || 0) || '0 B';
    const miniBars = function(values, tone) {
      const arr = (values || []).slice(-12);
      const max = Math.max.apply(null, arr.concat([1]));
      return '<div class="router-kpi-bars tone-' + tone + '">' + arr.map(function(v) {
        const level = Math.max(8, Math.round((Number(v || 0) / max) * 100));
        return '<i style="height:' + level + '%"></i>';
      }).join('') + '</div>';
    };
    const cards = [
      {
        code: 'M01',
        tone: 'amber',
        label: '调用 · 7 天',
        value: fmtNum(d7.calls || 0),
        unit: '',
        delta: (d1.calls || 0) + ' / 24 小时',
        spark: callSpark,
      },
      {
        code: 'M02',
        tone: 'neutral',
        label: 'TOKENS · 7 天',
        value: fmtNum(d7.total_tokens || 0),
        unit: '',
        delta: fmtNum(d1.total_tokens || 0) + ' / 24 小时',
        spark: tokenSpark,
      },
      {
        code: 'M03',
        tone: 'amber',
        label: '花费 · 30 天',
        value: fmtCurrency(lim.spent_30d_cny || d30.cost_cny || 0).replace('¥ ', '¥'),
        unit: '',
        delta: (lim.monthly_budget_cny > 0 ? ('预算 ' + fmtCurrency(lim.monthly_budget_cny)) : '预算未设'),
        spark: costSpark,
      },
      {
        code: 'M04',
        tone: stats.done > 0 ? 'green' : 'neutral',
        label: '章节进度',
        value: stats.done + '/' + stats.planned,
        unit: '章',
        delta: '写作中 ' + stats.writing + ' · 待推进 ' + stats.remaining,
        spark: [stats.done, stats.writing, stats.outlined, stats.planned],
      },
      {
        code: 'M05',
        tone: errRate > 0 ? 'red' : 'green',
        label: '错误率 · 7 天',
        value: errRate.toFixed(1),
        unit: '%',
        delta: '成功率 ' + successRate.toFixed(1) + '%',
        spark: daily.map(function(d) { return d.calls || 0; }),
      },
      {
        code: 'M06',
        tone: 'amber',
        label: '数据库 · SQLITE',
        value: dbSize.replace(' ', ''),
        unit: '',
        delta: (h.backup_count || 0) + ' 份备份',
        spark: [h.db_size_bytes || 0, h.log_size_bytes || 0, h.backup_count || 0],
      },
    ];
    const meta = $('monitor-kpi-meta');
    if (meta) meta.textContent = '共 ' + cards.length + ' 项';
    target.innerHTML = cards.map((card) => (
      '<section class="router-kpi-card tone-' + card.tone + '" tabindex="0">' +
        '<div class="router-kpi-code">' + escapeHtml(card.code) + '</div>' +
        '<div class="router-kpi-label">' + escapeHtml(card.label) + '</div>' +
        '<div class="router-kpi-value"><b>' + escapeHtml(card.value) + '</b>' + (card.unit ? '<span>' + escapeHtml(card.unit) + '</span>' : '') + '</div>' +
        '<div class="router-kpi-delta">' + escapeHtml(card.delta) + '</div>' +
        miniBars(card.spark, card.tone) +
      '</section>'
    )).join('');
  }

  function renderMonitorTrend(daily) {
    const target = $('monitor-daily');
    if (!target) return;
    daily = daily || [];
    if (!daily.length) {
      target.classList.add('empty');
      target.textContent = '暂无消耗数据';
      return;
    }
    target.classList.remove('empty');
    daily = daily.slice(-14);
    const costValues = daily.map((d) => Number(d.cost || 0));
    const tokenValues = daily.map((d) => Number(d.tokens || 0));
    const callValues = daily.map((d) => Number(d.calls || 0));
    const totalCost = costValues.reduce((a, b) => a + b, 0);
    const totalTokens = tokenValues.reduce((a, b) => a + b, 0);
    const totalCalls = callValues.reduce((a, b) => a + b, 0);
    const maxCost = Math.max.apply(null, costValues.concat([1]));
    const maxTokens = Math.max.apply(null, tokenValues.concat([1]));
    const maxCalls = Math.max.apply(null, callValues.concat([1]));
    const W = 1140;
    const H = 420;
    const padL = 52;
    const padR = 28;
    const padT = 34;
    const padB = 60;
    const innerW = W - padL - padR;
    const innerH = H - padT - padB;
    const step = innerW / Math.max(1, daily.length);
    const barW = Math.max(18, Math.min(70, step * 0.64));
    const point = function(v, i, max) {
      const x = padL + i * step + step / 2;
      const y = padT + innerH - (Number(v || 0) / max) * innerH;
      return [x, y];
    };
    const tokenPts = tokenValues.map(function(v, i) { return point(v, i, maxTokens); });
    const callPts = callValues.map(function(v, i) { return point(v, i, maxCalls); });
    const tokenPath = tokenPts.map(function(p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ');
    const callPath = callPts.map(function(p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ');
    const grid = [];
    for (let i = 0; i <= 4; i++) {
      const y = padT + (innerH / 4) * i;
      const v = maxCost * (1 - i / 4);
      grid.push('<line class="router-chart-gridline" x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '"></line>');
      grid.push('<text class="router-chart-y" x="' + (padL - 12) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end">' + escapeHtml(v.toFixed(v >= 10 ? 0 : 1)) + '</text>');
    }
    const bars = daily.map((d, i) => {
      const v = costValues[i];
      const h = (v / maxCost) * innerH;
      const x = padL + i * step + (step - barW) / 2;
      const y = padT + innerH - h;
      const day = String(d.day || '').slice(5);
      const title = (d.day || '') + '\\n花费：' + fmtCurrency(d.cost || 0) + '\\n调用：' + fmtNum(d.calls || 0) + '\\nToken：' + fmtNum(d.tokens || 0);
      return '<g class="router-chart-bar-group">' +
        '<rect class="router-chart-bar" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + Math.max(2, h).toFixed(1) + '" rx="0"><title>' + escapeHtml(title) + '</title></rect>' +
        '<text class="router-chart-x" x="' + (x + barW / 2).toFixed(1) + '" y="' + (H - 24) + '" text-anchor="middle">' + escapeHtml(day) + '</text>' +
      '</g>';
    }).join('');
    const tokenDots = tokenPts.map(function(p, i) {
      return '<circle class="router-chart-dot token" cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="4"><title>' + escapeHtml((daily[i].day || '') + '\\nTokens：' + fmtNum(tokenValues[i])) + '</title></circle>';
    }).join('');
    const callDots = callPts.map(function(p, i) {
      return '<circle class="router-chart-dot calls" cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="3"><title>' + escapeHtml((daily[i].day || '') + '\\n调用：' + fmtNum(callValues[i])) + '</title></circle>';
    }).join('');
    const latest = daily[daily.length - 1] || {};
    const peakCost = Math.max.apply(null, costValues.concat([0]));
    const lowCost = costValues.length ? Math.min.apply(null, costValues) : 0;
    target.innerHTML =
      '<div class="router-trend-shell">' +
        '<div class="router-trend-main">' +
          '<svg class="router-bar-svg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' +
            grid.join('') + bars +
            '<polyline class="router-chart-line token" points="' + tokenPath + '"></polyline>' +
            '<polyline class="router-chart-line calls" points="' + callPath + '"></polyline>' +
            tokenDots + callDots +
          '</svg>' +
        '</div>' +
        '<aside class="router-trend-side">' +
          '<div><span><i class="cost"></i>花费 (CNY)</span><b>' + escapeHtml(fmtCurrency(totalCost)) + '</b></div>' +
          '<div><span><i class="token"></i>Tokens</span><b>' + escapeHtml(fmtNum(totalTokens)) + '</b></div>' +
          '<div><span><i class="calls"></i>调用数</span><b>' + escapeHtml(fmtNum(totalCalls)) + '</b></div>' +
          '<hr>' +
          '<div><span>峰值日</span><b>' + escapeHtml(fmtCurrency(peakCost)) + '</b></div>' +
          '<div><span>谷值日</span><b>' + escapeHtml(fmtCurrency(lowCost)) + '</b></div>' +
          '<div><span>最新</span><b>' + escapeHtml((latest.day || '').slice(5) || '—') + '</b></div>' +
        '</aside>' +
      '</div>';
  }

  function renderMonitorQuota(mon, conc) {
    const target = $('monitor-quota');
    if (!target) return;
    mon = mon || {};
    const lim = mon.limits || {};
    const budgetPct = Math.max(0, Math.min(100, Number(lim.monthly_budget_used_pct || 0)));
    const tokenPct = Math.max(0, Math.min(100, Number(lim.daily_token_used_pct || 0)));
    const cMax = (conc && conc.max_concurrent) || 0;
    const cUse = (conc && conc.in_use) || 0;
    const cPct = cMax > 0 ? Math.min(100, (cUse / cMax) * 100) : 0;
    const rows = [
      { tag: '预算', title: '月度预算', value: fmtCurrency(lim.spent_30d_cny || 0), sub: '上限 ' + (lim.monthly_budget_cny > 0 ? fmtCurrency(lim.monthly_budget_cny) : '未设'), pct: budgetPct, tone: 'blue' },
      { tag: 'TOK', title: '24h Token', value: fmtNum(lim.tokens_24h || 0), sub: '上限 ' + (lim.daily_token_limit > 0 ? fmtNum(lim.daily_token_limit) : '未设'), pct: tokenPct, tone: 'pink' },
      { tag: '并发', title: '并发槽位', value: cUse + ' / ' + cMax, sub: cMax > 0 ? ('空闲 ' + Math.max(0, cMax - cUse)) : '未读取', pct: cPct, tone: 'green' },
    ];
    target.classList.remove('empty');
    target.innerHTML = '<div class="router-quota-list">' + rows.map((r) => (
      '<div class="router-quota-row tone-' + r.tone + '" tabindex="0">' +
        '<span class="router-quota-tag">' + escapeHtml(r.tag) + '</span>' +
        '<div class="router-quota-info"><strong>' + escapeHtml(r.title) + '</strong><span>' + escapeHtml(r.sub) + '</span><i><b style="width:' + r.pct.toFixed(1) + '%"></b></i></div>' +
        '<em>' + escapeHtml(r.value) + '</em>' +
      '</div>'
    )).join('') + '</div>';
  }

  function renderMonitorCompass(mon, conc) {
    const target = $('monitor-health');
    if (!target) return;
    mon = mon || {};
    const h = mon.health || {};
    const ln = mon.long_novel || {};
    const stats = longNovelProgressStats(ln);
    const sch = mon.schedule || {};
    const cMax = (conc && conc.max_concurrent) || 0;
    const cUse = (conc && conc.in_use) || 0;
    const cells = [
      { label: '数据库', value: h.db_path ? '就绪' : '未知', sub: h.db_path || '-', tone: h.db_path ? 'ok' : 'warn' },
      { label: '模型', value: mon.model || '未配置', sub: mon.dry_run ? 'Dry-run 模式' : 'Live 模式', tone: mon.model ? 'ok' : 'warn' },
      { label: '调度器', value: sch.enabled ? '启用' : '手动', sub: sch.cron || sch.interval || '-', tone: sch.enabled ? 'ok' : 'warn' },
      { label: '长篇小说', value: (ln.books_total || 0) + ' 本', sub: stats.done + '/' + stats.planned + ' 章 · ' + fmtNum(ln.words_total || 0) + '字', tone: (ln.books_total || 0) > 0 ? 'ok' : 'warn' },
      { label: '并发槽', value: cMax > 0 ? (cUse + ' / ' + cMax) : '-', sub: cMax > 0 ? (cUse >= cMax ? '已满载' : '有空位') : '未读取', tone: cMax > 0 ? (cUse >= cMax ? 'warn' : 'ok') : 'warn' },
    ];
    target.classList.remove('empty');
    target.innerHTML = '<div class="router-health-list">' + cells.map((c) => (
      '<div class="router-health-row tone-' + c.tone + '" tabindex="0">' +
        '<span></span><div><strong>' + escapeHtml(c.label) + '</strong><em>' + escapeHtml(c.sub) + '</em></div><b>' + escapeHtml(c.value) + '</b>' +
      '</div>'
    )).join('') + '</div>';
  }

  document.addEventListener('DOMContentLoaded', init);
})();
