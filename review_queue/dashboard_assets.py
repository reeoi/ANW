"""Static assets (CSS, HTML body template, JS) for the ANP Local Studio dashboard.

These are kept in their own module so :mod:`review_queue.human_review` stays
focused on routing/business logic. Templates use ``__PLACEHOLDER__`` style
markers consumed via :py:meth:`str.replace` to avoid f-string brace conflicts
with embedded CSS / JS.
"""

from __future__ import annotations


DASHBOARD_CSS = """

@import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@500;700;900&family=Inter:wght@400;500;600;700&family=Courier+Prime:wght@400;700&display=swap');

:root {
  --bg: #041c1c;
  --bg-soft: #082626;
  --panel: #082626;
  --panel-soft: #051c1c;
  --text: #ffe6cb;
  --text-muted: rgba(255, 230, 203, 0.72);
  --muted: rgba(255, 230, 203, 0.55);
  --border: rgba(255, 230, 203, 0.22);
  --border-strong: rgba(255, 215, 0, 0.45);
  --primary: #ffd700;
  --primary-strong: #ffe066;
  --primary-soft: rgba(255, 215, 0, 0.12);
  --primary-glow: rgba(255, 215, 0, 0.32);
  --primary-border: rgba(255, 215, 0, 0.5);
  --primary-contrast: #041c1c;
  --accent: #6ee7b7;
  --success: #6ee7b7;
  --success-soft: rgba(110, 231, 183, 0.12);
  --success-border: rgba(110, 231, 183, 0.45);
  --warning: #f5b942;
  --warning-soft: rgba(245, 185, 66, 0.14);
  --warning-border: rgba(245, 185, 66, 0.5);
  --danger: #ff7a7a;
  --danger-soft: rgba(255, 122, 122, 0.14);
  --danger-border: rgba(255, 122, 122, 0.5);
  --info: #7dd3fc;
  --sidebar-bg: #031414;
  --sidebar-text: rgba(255, 230, 203, 0.6);
  --sidebar-active: #ffe6cb;
  --card-bg: rgba(8, 38, 38, 0.7);
  --grid-line: rgba(255, 230, 203, 0.06);
  --hover: rgba(255, 215, 0, 0.08);
  --radius: 14px;
  --radius-sm: 10px;
  --shadow: 0 6px 20px rgba(0, 0, 0, 0.32);
  --shadow-md: 0 10px 30px rgba(0, 0, 0, 0.4);
  --shadow-lg: 0 20px 50px rgba(0, 0, 0, 0.55);
  --font-serif: 'Noto Serif SC', 'Source Han Serif SC', 'Songti SC', 'STSong', serif;
  --font-sans: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-mono: 'Courier Prime', 'JetBrains Mono', 'Fira Code', ui-monospace, Consolas, monospace;
}


[data-theme='dark'] {
  --bg: #041c1c;
  --bg-soft: #082626;
  --panel: #082626;
  --panel-soft: #051c1c;
  --text: #ffe6cb;
  --text-muted: rgba(255, 230, 203, 0.72);
  --muted: rgba(255, 230, 203, 0.55);
  --border: rgba(255, 230, 203, 0.22);
  --primary: #ffd700;
  --primary-strong: #ffe066;
  --primary-soft: rgba(255, 215, 0, 0.12);
  --success: #6ee7b7;
  --success-soft: rgba(110, 231, 183, 0.12);
  --warning: #f5b942;
  --warning-soft: rgba(245, 185, 66, 0.14);
  --danger: #ff7a7a;
  --danger-soft: rgba(255, 122, 122, 0.14);
  --info: #7dd3fc;
  --sidebar-bg: #031414;
  --sidebar-text: rgba(255, 230, 203, 0.6);
  --sidebar-active: #ffe6cb;
  --card-bg: rgba(8, 38, 38, 0.7);
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); transition: background-color 0.4s, color 0.4s; }
body {
  font-family: var(--font-sans);
  color: var(--text);
  line-height: 1.65;
  -webkit-font-smoothing: antialiased;
  background:
    radial-gradient(circle at 0% 0%, rgba(255, 215, 0, 0.04) 0%, transparent 50%),
    radial-gradient(circle at 100% 100%, rgba(110, 231, 183, 0.03) 0%, transparent 50%),
    var(--bg);
  background-attachment: fixed;
  min-height: 100vh;
}

/* ----------- Layout & Sidebar ----------- */

.layout { display: flex; min-height: 100vh; }

.sidebar {
  width: 260px;
  background: var(--sidebar-bg);
  color: var(--sidebar-text);
  padding: 1.75rem 1.1rem;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
}

.sidebar h1 {
  font-family: var(--font-serif);
  font-size: 1.85rem;
  font-weight: 900;
  margin: 0 0.4rem 1.8rem;
  display: flex;
  align-items: center;
  gap: 0.85rem;
  color: var(--text);
  letter-spacing: 0.04em;
  position: relative;
  padding-bottom: 1.2rem;
}
.sidebar h1::after {
  content: "";
  position: absolute;
  left: 0.4rem;
  right: 0.4rem;
  bottom: 0;
  height: 1px;
  background: linear-gradient(90deg, var(--primary-border) 0%, transparent 90%);
}
.sidebar h1 svg {
  color: var(--primary) !important;
  filter: drop-shadow(0 0 8px var(--primary-glow));
}
.sidebar h1 > span {
  font-family: var(--font-sans) !important;
  font-size: 0.66rem !important;
  font-weight: 600 !important;
  color: var(--muted) !important;
  letter-spacing: 0.22em !important;
  text-transform: uppercase;
  margin-top: 0.15rem;
}

.sidebar nav { display: flex; flex-direction: column; gap: 0.2rem; }

.sidebar nav button {
  background: transparent;
  color: var(--sidebar-text);
  border: 0;
  text-align: left;
  padding: 0.7rem 1rem;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.92rem;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 0.8rem;
  letter-spacing: 0.04em;
  transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
  position: relative;
}
.sidebar nav button:hover { background: var(--hover); color: var(--text); transform: none; }
.sidebar nav button.active {
  background: var(--primary-soft);
  color: var(--text);
  border-left: 3px solid var(--primary);
  border-radius: 4px 8px 8px 4px;
  padding-left: calc(1rem - 3px);
  font-weight: 600;
}
.sidebar nav button.active svg { color: var(--primary); }
.sidebar nav button svg { flex-shrink: 0; opacity: 0.85; }

.sidebar .footnote {
  margin-top: auto;
  padding: 1.25rem 0.4rem 0;
  font-size: 0.74rem;
  color: var(--muted);
  border-top: 1px solid var(--border);
  line-height: 1.55;
}
.sidebar .footnote code {
  background: var(--panel-soft);
  border: 1px solid var(--border);
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 0.72rem;
  color: var(--primary-strong);
}
.sidebar-footer { margin-top: auto; padding-top: 1rem; border-top: 1px solid var(--border); }
.nav-group-header {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  padding: 1rem 0.5rem 0.3rem 0.5rem;
  border-top: 1px solid var(--border);
  margin-top: 0.5rem;
}
.nav-group-header:first-of-type { border-top: none; margin-top: 0; }
.system-status {
  display: flex; align-items: center; gap: 0.5rem;
  font-size: 0.76rem; color: var(--muted);
  margin-bottom: 0.85rem; padding: 0 0.4rem;
  letter-spacing: 0.05em;
}
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
.status-dot.active {
  background: var(--success);
  box-shadow: 0 0 8px var(--success), 0 0 14px rgba(110, 231, 183, 0.45);
  animation: pulse 2.6s ease-in-out infinite;
}
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }

.theme-toggle {
  margin-top: 1rem;
  margin-bottom: 1rem;
  padding: 0.7rem 1rem;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.7rem;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--sidebar-text);
  border: 1px solid var(--border);
  background: var(--panel-soft);
  transition: all 0.2s;
}
.theme-toggle:hover {
  background: var(--hover);
  color: var(--text);
  border-color: var(--primary-border);
  transform: none;
}

/* ----------- Main ----------- */

main {
  flex: 1;
  padding: 2.5rem 3.5rem 4rem;
  max-width: 1600px;
  margin: 0 auto;
  width: 100%;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 2.5rem;
  gap: 1.5rem;
  flex-wrap: wrap;
  position: relative;
  padding-bottom: 1.2rem;
}
.page-header::after {
  content: "";
  position: absolute;
  left: 0; right: 0; bottom: 0;
  height: 1px;
  background: linear-gradient(90deg, var(--primary-border) 0%, var(--border) 35%, transparent 100%);
}
.page-header h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 2.3rem;
  font-weight: 900;
  letter-spacing: 0.02em;
  color: var(--text);
  position: relative;
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}
.page-header h2[data-kicker]::before {
  content: attr(data-kicker);
  font-family: var(--font-sans);
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.34em;
  color: var(--primary);
  text-transform: uppercase;
  margin-bottom: 0.55rem;
  display: block;
}
.page-header .meta {
  color: var(--text-muted);
  font-size: 0.85rem;
  font-family: var(--font-mono);
}

.meta { color: var(--text-muted); font-size: 0.85rem; }
.empty { color: var(--muted); padding: 1.5rem; text-align: center; font-style: italic; }
.hint { color: var(--muted); font-size: 0.85rem; }
.hidden { display: none !important; }

.banner {
  background: var(--primary-soft);
  color: var(--text);
  border: 1px solid var(--primary-border);
  border-left: 4px solid var(--primary);
  padding: 0.95rem 1.2rem;
  border-radius: var(--radius-sm);
  margin-bottom: 1.5rem;
  font-weight: 500;
}
.banner.hidden { display: none !important; }
.section { display: none; animation: fadeIn 0.4s cubic-bezier(0.16, 1, 0.3, 1); }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.section.active { display: block; }

/* ----------- Cards & Panels (hermes cell) ----------- */

.card-glass {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.5rem;
  box-shadow: var(--shadow);
  transition: all 0.25s ease;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
.card-glass:hover { transform: translateY(-3px); box-shadow: var(--shadow-lg); border-color: var(--primary-border); }

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}
.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.4rem 1.5rem;
  box-shadow: var(--shadow);
  transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
  position: relative;
  overflow: hidden;
}
.card::before {
  content: "";
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 2px;
  background: var(--primary);
  opacity: 0;
  transition: opacity 0.2s;
}
.card:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-md);
  border-color: var(--primary-border);
}
.card:hover::before { opacity: 1; }
.card .label {
  color: var(--muted);
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.18em;
}
.card .value {
  font-family: var(--font-serif);
  font-size: 2rem;
  font-weight: 900;
  margin-top: 0.5rem;
  color: var(--text);
  letter-spacing: 0.01em;
}
.card.warn .value { color: var(--warning); }
.card.bad .value { color: var(--danger); }
.card.ok .value { color: var(--success); }

.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.75rem 1.85rem;
  box-shadow: var(--shadow);
  margin-bottom: 1.5rem;
  position: relative;
  transition: border-color 0.2s;
}
.panel:hover { border-color: var(--primary-border); }
.panel h3 {
  margin: 0 0 1.25rem;
  font-family: var(--font-serif);
  font-size: 1.3rem;
  font-weight: 800;
  color: var(--text);
  letter-spacing: 0.02em;
  display: flex;
  align-items: center;
  gap: 0.55rem;
}
.panel p { margin: 0 0 1rem; color: var(--text-muted); }

/* ----------- Forms ----------- */

form.grid { display: grid; gap: 1.25rem; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
form.grid .full { grid-column: 1 / -1; }
label {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text);
}
input, textarea, select {
  font: inherit;
  font-family: var(--font-sans);
  padding: 0.72rem 1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  color: var(--text);
  width: 100%;
  transition: all 0.2s;
}
input::placeholder, textarea::placeholder { color: var(--muted); }
input:focus, textarea:focus, select:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 4px var(--primary-soft);
}
textarea {
  min-height: 200px;
  resize: vertical;
  font-family: var(--font-mono);
  font-size: 0.86rem;
  line-height: 1.65;
}

/* ----------- Buttons ----------- */

.actions { display: flex; flex-wrap: wrap; gap: 0.75rem; }
button {
  font: inherit;
  padding: 0.62rem 1.3rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  cursor: pointer;
  background: var(--panel-soft);
  color: var(--text);
  font-weight: 600;
  letter-spacing: 0.02em;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.55rem;
}
button:hover {
  background: var(--hover);
  border-color: var(--primary-border);
  transform: translateY(-1px);
}
button:active { transform: translateY(0) scale(0.98); }
button:disabled { opacity: 0.4; cursor: not-allowed; transform: none !important; }

button.primary, .btn-primary {
  background: var(--primary);
  color: var(--primary-contrast);
  border: 1px solid var(--primary);
  font-weight: 700;
  box-shadow: 0 4px 14px var(--primary-glow);
}
button.primary:hover, .btn-primary:hover {
  background: var(--primary-strong);
  border-color: var(--primary-strong);
  color: var(--primary-contrast);
  box-shadow: 0 8px 22px var(--primary-glow);
}

button.success {
  background: var(--success);
  color: var(--primary-contrast);
  border-color: var(--success);
  font-weight: 700;
}
button.success:hover { filter: brightness(1.12); box-shadow: 0 6px 18px var(--success-soft); }

button.danger, .btn-danger {
  background: transparent;
  color: var(--danger);
  border: 1px solid var(--danger-border);
  font-weight: 600;
}
button.danger:hover, .btn-danger:hover {
  background: var(--danger-soft);
  color: var(--danger);
  border-color: var(--danger);
}

button.warning {
  background: var(--warning);
  color: var(--primary-contrast);
  border-color: var(--warning);
  font-weight: 700;
}
button.warning:hover { filter: brightness(1.12); box-shadow: 0 6px 18px var(--warning-soft); }

button.ghost {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
}
button.ghost:hover {
  background: var(--hover);
  border-color: var(--primary-border);
  color: var(--text);
}

button.tiny {
  padding: 0.32rem 0.72rem;
  font-size: 0.78rem;
  border-radius: 6px;
  letter-spacing: 0.03em;
}

/* ----------- Tables ----------- */

table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
table th, table td { padding: 1rem 1rem; border-bottom: 1px solid var(--border); text-align: left; vertical-align: middle; }
table th {
  color: var(--muted);
  font-weight: 700;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.18em;
  background: var(--panel-soft);
}
table tr:hover { background: var(--panel-soft); }

/* ----------- Badges ----------- */

.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.25rem 0.8rem;
  border-radius: 9999px;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  background: var(--panel-soft);
  color: var(--text-muted);
  border: 1px solid var(--border);
}
.badge-indigo {
  background: var(--primary-soft);
  color: var(--primary);
  padding: 0.2rem 0.6rem;
  border-radius: 6px;
  font-weight: 700;
  font-size: 0.72rem;
  border: 1px solid var(--primary-border);
}
.badge.pending { background: var(--warning-soft); color: var(--warning); border-color: var(--warning-border); }
.badge.needs_human { background: var(--danger-soft); color: var(--danger); border-color: var(--danger-border); }
.badge.approved { background: var(--success-soft); color: var(--success); border-color: var(--success-border); }
.badge.published { background: rgba(125, 211, 252, 0.14); color: var(--info); border-color: rgba(125, 211, 252, 0.45); }
.badge.rejected { background: var(--panel-soft); color: var(--muted); border-color: var(--border); }
.badge.publish_paused { background: var(--warning-soft); color: var(--warning); border-color: var(--warning-border); }
.badge.publish_failed, .badge.failed { background: var(--danger-soft); color: var(--danger); border-color: var(--danger-border); }

/* ----------- Log / Code ----------- */

pre.log {
  white-space: pre-wrap;
  background: var(--panel-soft);
  color: var(--text);
  padding: 1.4rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 520px;
  overflow: auto;
  font-size: 0.83rem;
  font-family: var(--font-mono);
  line-height: 1.75;
}
code {
  font-family: var(--font-mono);
  background: var(--panel-soft);
  border: 1px solid var(--border);
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  font-size: 0.86em;
  color: var(--primary-strong);
}

/* ----------- Toasts ----------- */

#toasts {
  position: fixed;
  right: 2rem;
  bottom: 2rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  z-index: 9999;
}
.toast {
  background: var(--panel);
  color: var(--text);
  padding: 1rem 1.35rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-lg);
  max-width: 420px;
  font-size: 0.92rem;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  animation: slideIn 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes slideIn { from { transform: translateX(120%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
.toast.success { border-left: 4px solid var(--success); }
.toast.error { border-left: 4px solid var(--danger); }
.toast.warn { border-left: 4px solid var(--warning); }

/* ----------- Modals ----------- */

.modal-backdrop {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(4, 28, 28, 0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  align-items: center;
  justify-content: center;
  padding: 2rem;
  z-index: 1000;
}
.modal-backdrop.show { display: flex; }
.modal {
  background: var(--panel);
  border: 1px solid var(--primary-border);
  border-radius: var(--radius);
  width: min(900px, 100%);
  max-height: 90vh;
  overflow: auto;
  padding: 2.25rem 2.5rem;
  box-shadow: 0 30px 60px rgba(0, 0, 0, 0.6), 0 0 80px rgba(255, 215, 0, 0.06);
  animation: modalIn 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes modalIn { from { transform: scale(0.95) translateY(10px); opacity: 0; } to { transform: scale(1) translateY(0); opacity: 1; } }
.modal h3 {
  margin-top: 0;
  font-family: var(--font-serif);
  font-size: 1.55rem;
  font-weight: 800;
  letter-spacing: 0.02em;
  color: var(--text);
}

/* ----------- Progress ----------- */

.progress {
  position: relative;
  height: 10px;
  background: var(--panel-soft);
  border: 1px solid var(--border);
  border-radius: 999px;
  overflow: hidden;
  margin: 0.5rem 0 1rem;
}
.progress > span {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--primary), var(--primary-strong));
  box-shadow: 0 0 12px var(--primary-glow);
  transition: width 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}

/* ----------- KV grid ----------- */

.kv-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1rem 2rem;
  font-size: 0.92rem;
}
.kv-grid div {
  display: flex;
  justify-content: space-between;
  gap: 1.5rem;
  padding: 0.7rem 0;
  border-bottom: 1px dotted var(--border);
}
.kv-grid div span:first-child { font-weight: 500; color: var(--muted); letter-spacing: 0.04em; }
.kv-grid div span:last-child { color: var(--text); font-family: var(--font-mono); font-weight: 500; }

/* ---------- Settings ---------- */

.settings-section { max-width: 1080px; }
.settings-group {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 1rem;
  box-shadow: var(--shadow);
  overflow: hidden;
  transition: border-color 0.2s;
}
.settings-group:hover { border-color: var(--primary-border); }
.settings-group > summary {
  list-style: none;
  cursor: pointer;
  padding: 1.15rem 1.5rem;
  display: flex;
  align-items: center;
  gap: 0.7rem;
  font-family: var(--font-serif);
  font-weight: 700;
  font-size: 1.05rem;
  color: var(--text);
  letter-spacing: 0.02em;
  user-select: none;
  background: linear-gradient(90deg, var(--panel-soft) 0%, var(--panel) 100%);
  transition: background 0.2s;
}
.settings-group > summary:hover { background: var(--hover); }
.settings-group > summary::-webkit-details-marker { display: none; }
.settings-group > summary::before {
  content: "▸";
  display: inline-block;
  color: var(--primary);
  font-size: 0.85rem;
  transition: transform 0.2s;
}
.settings-group[open] > summary::before { transform: rotate(90deg); }
.settings-group .group-body {
  padding: 0.6rem 1.5rem 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 1.2rem;
}
.settings-group .group-actions {
  display: flex;
  gap: 0.75rem;
  justify-content: flex-end;
  border-top: 1px dotted var(--border);
  padding-top: 1.1rem;
  margin-top: 0.4rem;
}
.settings-row { display: grid; grid-template-columns: 14rem 1fr; gap: 0.75rem 1.5rem; align-items: center; }
.settings-row > label {
  font-size: 0.92rem;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 0.02em;
}
.settings-row .control-inline { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }

.settings-advanced {
  margin-top: 0.75rem;
  border-top: 1px dotted var(--border);
  padding-top: 0.9rem;
}
.settings-advanced > summary {
  list-style: none;
  cursor: pointer;
  font-size: 0.84rem;
  font-weight: 600;
  color: var(--muted);
  display: flex;
  align-items: center;
  gap: 0.4rem;
  letter-spacing: 0.04em;
  padding: 0.3rem 0;
}
.settings-advanced > summary::-webkit-details-marker { display: none; }
.settings-advanced > summary::before { content: "+ "; color: var(--primary); font-weight: 700; }
.settings-advanced[open] > summary::before { content: "− "; }
.settings-advanced > summary:hover { color: var(--text); text-decoration: none; }

.secret-input {
  display: flex;
  align-items: stretch;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  overflow: hidden;
}
.secret-input input { border: 0; background: transparent; flex: 1; }
.secret-input .eye {
  background: transparent;
  border: 0;
  border-left: 1px solid var(--border);
  padding: 0 1rem;
  cursor: pointer;
  color: var(--muted);
  transform: none;
}
.secret-input .eye:hover { color: var(--primary); background: var(--hover); transform: none; }

.toggle-switch {
  display: inline-flex;
  align-items: center;
  gap: 0.7rem;
  cursor: pointer;
  user-select: none;
  font-weight: 500;
}
.toggle-switch input { display: none; width: auto; }
.toggle-track {
  position: relative;
  width: 42px;
  height: 22px;
  background: var(--panel-soft);
  border: 1px solid var(--border);
  border-radius: 999px;
  transition: all 0.2s;
}
.toggle-track::after {
  content: "";
  position: absolute;
  top: 2px; left: 2px;
  width: 16px; height: 16px;
  border-radius: 50%;
  background: var(--muted);
  transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}
.toggle-switch input:checked + .toggle-track {
  background: var(--primary-soft);
  border-color: var(--primary);
}
.toggle-switch input:checked + .toggle-track::after {
  left: 22px;
  background: var(--primary);
  box-shadow: 0 0 8px var(--primary-glow);
}
.form-group { display: flex; flex-direction: column; gap: 0.4rem; }

/* ---------- Status Cards ---------- */

.status-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}
.card-status {
  position: relative;
  background: var(--panel);
  border: 1px solid var(--border);
  border-top: 3px solid var(--success);
  border-radius: var(--radius);
  padding: 1.4rem 1.5rem;
  box-shadow: var(--shadow);
  cursor: pointer;
  transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}
.card-status:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-md);
  border-color: var(--primary-border);
  border-top-color: var(--primary);
}
.card-status .card-icon {
  position: absolute;
  top: 1.1rem; right: 1.25rem;
  font-size: 1.4rem;
  opacity: 0.3;
}
.card-status .card-label {
  color: var(--muted);
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.18em;
}
.card-status .card-value {
  font-family: var(--font-serif);
  font-size: 1.5rem;
  font-weight: 800;
  margin-top: 0.5rem;
  color: var(--text);
}
.card-status .card-detail {
  color: var(--muted);
  font-size: 0.85rem;
  margin-top: 0.45rem;
  line-height: 1.45;
}
.card-status.level-ok { border-top-color: var(--success); }
.card-status.level-warn { border-top-color: var(--warning); }
.card-status.level-warn .card-value { color: var(--warning); }
.card-status.level-danger { border-top-color: var(--danger); }
.card-status.level-danger .card-value { color: var(--danger); }

/* ---------- DryRun ---------- */

.story-dryrun {
  border-left: 3px solid var(--primary) !important;
  background: linear-gradient(90deg, var(--primary-soft) 0%, transparent 60%) !important;
}
.dryrun-marker {
  display: inline-block;
  margin-right: 0.3rem;
  padding: 0.1rem 0.5rem;
  font-size: 0.68rem;
  background: var(--primary-soft);
  color: var(--primary);
  border: 1px solid var(--primary-border);
  border-radius: 4px;
  font-weight: 700;
  vertical-align: middle;
  letter-spacing: 0.08em;
}

/* ---------- Critical Banner ---------- */

#critical-banner {
  position: relative;
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid var(--danger-border);
  border-left: 4px solid var(--danger);
  border-radius: var(--radius-sm);
  padding: 0.85rem 1.1rem;
  margin-bottom: 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
#critical-banner.hidden { display: none; }
#critical-banner .crit-row { display: flex; gap: 0.6rem; align-items: center; }
#critical-banner .crit-icon { font-size: 1.1rem; }
#critical-banner .crit-text { flex: 1; font-weight: 500; color: var(--text); }
#critical-banner button.dismiss {
  background: transparent;
  border: 1px solid var(--danger-border);
  color: var(--danger);
}
#critical-banner button.dismiss:hover {
  background: var(--danger-soft);
  border-color: var(--danger);
  color: var(--danger);
}

/* ---------- Monitor section ---------- */

.monitor-section {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.5rem 1.75rem;
  margin-bottom: 1.5rem;
  box-shadow: var(--shadow);
  transition: border-color 0.2s;
}
.monitor-section:hover { border-color: var(--primary-border); }
.monitor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.2rem;
  flex-wrap: wrap;
  gap: 0.75rem;
}
.monitor-header h3 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 1.2rem;
  font-weight: 800;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 0.55rem;
  letter-spacing: 0.02em;
}
.monitor-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.85rem;
}
.monitor-card {
  display: flex;
  align-items: center;
  gap: 0.85rem;
  padding: 0.95rem 1.1rem;
  background: var(--panel-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  transition: all 0.2s;
}
.monitor-card:hover {
  border-color: var(--primary-border);
  background: var(--hover);
}
.monitor-icon-wrap {
  font-size: 1.4rem;
  width: 38px;
  height: 38px;
  background: var(--primary-soft);
  border: 1px solid var(--primary-border);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.monitor-info { display: flex; flex-direction: column; gap: 0.15rem; min-width: 0; }
.m-label { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.16em; font-weight: 700; }
.m-value { font-size: 1rem; font-weight: 600; color: var(--text); }

#console-panel .console-actions { display: flex; gap: 0.6rem; align-items: center; flex-wrap: wrap; margin-bottom: 0.8rem; }
#console-panel .console-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.6rem; }
#console-panel .console-cell {
  background: var(--panel-soft);
  padding: 0.6rem 0.85rem;
  border-radius: 6px;
  border: 1px solid var(--border);
}
#console-panel .console-label { font-size: 0.72rem; color: var(--muted); margin-bottom: 0.25rem; text-transform: uppercase; letter-spacing: 0.12em; font-weight: 600; }
#console-panel .console-value { font-size: 1rem; font-weight: 500; }

/* ---------- Inbox ---------- */

#inbox-panel { margin-top: 1rem; }
#inbox-panel .inbox-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.85rem; }
#inbox-panel h3 { margin: 0; }
#inbox-panel #inbox-summary { color: var(--muted); font-size: 0.85rem; font-weight: 400; }
.inbox-head { display: flex; justify-content: space-between; align-items: center; }
.inbox-row {
  display: grid;
  grid-template-columns: 60px 1fr auto auto auto;
  align-items: center;
  gap: 0.7rem;
  padding: 0.6rem 0.7rem;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.18s;
  border: 1px solid transparent;
}
.inbox-row:hover { background: var(--hover); border-color: var(--border); }
.inbox-row + .inbox-row { margin-top: 0.2rem; }
.inbox-row .inbox-id { color: var(--muted); font-family: var(--font-mono); font-size: 0.82rem; }
.inbox-row .inbox-title {
  font-weight: 500;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inbox-row .inbox-meta { color: var(--muted); font-size: 0.76rem; font-family: var(--font-mono); }
.inbox-row .inbox-badge {
  font-size: 0.7rem;
  padding: 0.18rem 0.6rem;
  border-radius: 999px;
  font-weight: 700;
  white-space: nowrap;
  letter-spacing: 0.04em;
  border: 1px solid var(--border);
}
.inbox-badge.pending { background: var(--warning-soft); color: var(--warning); border-color: var(--warning-border); }
.inbox-badge.needs_human { background: var(--danger-soft); color: var(--danger); border-color: var(--danger-border); }
.inbox-badge.approved { background: var(--success-soft); color: var(--success); border-color: var(--success-border); }
.inbox-badge.published { background: rgba(125, 211, 252, 0.14); color: var(--info); border-color: rgba(125, 211, 252, 0.45); }
.inbox-badge.publish_paused { background: var(--warning-soft); color: var(--warning); border-color: var(--warning-border); }
.inbox-badge.failed { background: var(--danger-soft); color: var(--danger); border-color: var(--danger-border); }
.inbox-badge.rejected { background: var(--panel-soft); color: var(--muted); border-color: var(--border); }
.inbox-badge.cancelled { background: var(--panel-soft); color: var(--muted); border-color: var(--border); }
.inbox-row .inbox-actions { display: flex; gap: 0.35rem; }
.inbox-row .inbox-actions button {
  font-size: 0.74rem;
  padding: 0.25rem 0.62rem;
  border-radius: 5px;
  cursor: pointer;
}
.inbox-empty { color: var(--muted); padding: 1.5rem; text-align: center; font-style: italic; }

/* 详情模态（轻量复用 story-modal 样式） */
#inbox-detail-modal {
  background: rgba(4, 28, 28, 0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  position: fixed;
  inset: 0;
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}
#inbox-detail-modal.show { display: flex; }
#inbox-detail-modal .modal-card {
  background: var(--panel);
  border: 1px solid var(--primary-border);
  border-radius: var(--radius);
  max-width: 900px;
  width: 100%;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 30px 60px rgba(0, 0, 0, 0.6);
}
#inbox-detail-modal .modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
}
#inbox-detail-modal .modal-body { margin: 0; padding: 1rem 1.25rem; overflow: auto; }
#inbox-detail-modal .modal-meta { display: flex; gap: 0.7rem; flex-wrap: wrap; color: var(--muted); font-size: 0.85rem; margin-bottom: 0.6rem; }
#inbox-detail-modal .modal-content {
  white-space: pre-wrap; word-break: break-word;
  font-family: var(--font-mono); font-size: 0.85rem;
  line-height: 1.7; max-height: 50vh; overflow: auto;
  background: var(--panel-soft); padding: 1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
#inbox-detail-modal .modal-foot {
  padding: 0.85rem 1.25rem;
  border-top: 1px solid var(--border);
  display: flex; justify-content: flex-end; gap: 0.5rem;
}

/* ---------- Phase strip ---------- */

#progress-panel { margin-top: 1rem; }
#progress-panel h3 { display: flex; align-items: baseline; gap: 0.6rem; }
#progress-panel #progress-story { font-size: 0.85rem; color: var(--muted); font-weight: 400; }
.phase-strip { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.85rem; }

.phase-chip {
  display: inline-flex; flex-direction: column; gap: 0.18rem;
  padding: 0.5rem 0.8rem; border-radius: 8px; min-width: 6.5rem;
  background: var(--panel-soft); border: 1px solid var(--border);
  font-size: 0.82rem; cursor: default; transition: all .18s;
  color: var(--text);
}
.phase-chip .phase-chip-label { font-weight: 700; letter-spacing: 0.02em; }
.phase-chip .phase-chip-meta { font-size: 0.7rem; color: var(--muted); font-family: var(--font-mono); }

.phase-chip[data-status="done"] {
    background: var(--success-soft); color: var(--success); border-color: var(--success-border); cursor: pointer;
}
.phase-chip[data-status="in_progress"], .phase-chip[data-status="running"] {
    background: var(--primary-soft); color: var(--primary); border-color: var(--primary-border);
}
.phase-chip[data-status="rewrite"] {
    background: var(--warning-soft); color: var(--warning); border-color: var(--warning-border);
}
.phase-chip[data-status="failed"] {
    background: var(--danger-soft); color: var(--danger); border-color: var(--danger-border); cursor: pointer;
}
.phase-chip[data-status="pending"] { color: var(--muted); opacity: 0.75; }

.phase-chip[data-clickable="1"]:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow);
    border-color: var(--primary);
}

.phase-section-sub { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 1rem; }

.phase-timeline-wrap {
  background: var(--panel-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 1rem 1.2rem;
}
.phase-timeline-head {
  font-family: var(--font-serif);
  font-weight: 700;
  color: var(--text);
  margin-bottom: 0.5rem;
  letter-spacing: 0.04em;
  font-size: 0.95rem;
}
.phase-timeline {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.83rem;
  font-family: var(--font-mono);
}
.phase-timeline li[data-status="failed"] .pt-label { color: var(--danger); }
.phase-timeline li[data-status="rewrite"] .pt-label { color: var(--warning); }
.phase-timeline li[data-status="in_progress"], .phase-timeline li[data-status="running"] .pt-label { color: var(--primary); }
.phase-timeline li.pt-attempt-head[data-status="failed"]      { color: var(--danger); }
.phase-timeline li.pt-attempt-head[data-status="in_progress"] { color: var(--primary); }
.phase-timeline li.pt-attempt-head[data-status="done"]        { color: var(--success); }
.phase-timeline-empty { color: var(--muted); font-size: 0.85rem; padding: 0.4rem 0; font-style: italic; }

.retry-banner {
  margin: 0 0 0.85rem 0;
  padding: 0.6rem 0.9rem;
  border-radius: var(--radius-sm);
  background: var(--warning-soft);
  color: var(--warning);
  font-size: 0.85rem;
  border: 1px solid var(--warning-border);
}
.retry-banner strong { color: var(--warning); font-weight: 700; }

/* ---------- Artifact modal ---------- */

.artifact-modal {
  position: fixed;
  inset: 0;
  background: rgba(4, 28, 28, 0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}
.artifact-modal-inner {
  background: var(--panel);
  border: 1px solid var(--primary-border);
  border-radius: var(--radius);
  max-width: 900px;
  width: 100%;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 30px 60px rgba(0, 0, 0, 0.6);
}
.artifact-modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
}
.artifact-modal-head #artifact-modal-title {
  font-weight: 700;
  font-family: var(--font-serif);
  color: var(--text);
  letter-spacing: 0.02em;
}
.artifact-modal-body {
  margin: 0;
  padding: 1.2rem 1.25rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono);
  font-size: 0.85rem;
  line-height: 1.75;
  color: var(--text);
  background: var(--panel-soft);
}

/* ---------- Mode confirm / login ---------- */

.mode-confirm-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  margin: 1rem 0;
  padding: 1rem;
  background: var(--panel-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.mode-confirm-list label {
  flex-direction: row;
  align-items: center;
  gap: 0.6rem;
  cursor: pointer;
  font-weight: 500;
}
.mode-confirm-list input[type=checkbox] { width: auto; cursor: pointer; accent-color: var(--primary); }

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.6rem;
  margin-top: 1.2rem;
  padding-top: 1rem;
  border-top: 1px dotted var(--border);
}

.login-status-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.65rem 0.9rem;
  background: var(--panel-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  flex-wrap: wrap;
}
.status-icon { font-size: 1.1rem; }

/* ---------- Scrollbars ---------- */

* {
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}
*::-webkit-scrollbar { width: 8px; height: 8px; }
*::-webkit-scrollbar-track { background: transparent; }
*::-webkit-scrollbar-thumb { background: var(--border); border-radius: 999px; }
*::-webkit-scrollbar-thumb:hover { background: var(--primary-border); }

/* ---------- Selection ---------- */

::selection { background: var(--primary-soft); color: var(--primary-strong); }

/* ---------- Responsive ---------- */

@media (max-width: 1000px) {
  .sidebar { width: 220px; padding: 1.3rem 0.85rem; }
  main { padding: 1.75rem 1.75rem 3rem; }
  .page-header h2 { font-size: 1.85rem; }
  .settings-row { grid-template-columns: 1fr; }
}
"""


DASHBOARD_BODY_TEMPLATE = """
<div class="layout">
  <aside class="sidebar">
    <h1 style="display:flex; flex-direction:column; align-items:flex-start; gap:0.2rem">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="color:var(--primary)"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>
      ANP<span style="font-size:0.7rem; color:var(--muted); font-weight:400; letter-spacing:0">AI 小说创作流水线</span>
    </h1>
    <nav id="nav">
      <button data-target="overview" class="active">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
        总览
      </button>
      <button data-target="monitor">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
        监控面板
      </button>
      <div class="nav-group-header">📦 功能模块</div>
      <button data-target="generate" data-feature="short-story">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
        📝 短篇小说
      </button>
      <button data-target="long-novel" data-feature="long-novel">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>
        📖 长篇小说
      </button>
      <button data-target="theme-pool" data-feature="theme-pool">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
        🎯 题材库
      </button>
      <div class="nav-group-header">⚙️ 系统</div>
      <button data-target="logs">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
        日志
      </button>
      <button data-target="settings-edit">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
        设置
      </button>
    </nav>
    <button id="theme-toggle" class="theme-toggle">
      <span id="theme-toggle-icon">🌙</span>
      <span id="theme-toggle-label">深色模式</span>
    </button>
    <div class="sidebar-footer"><div class="system-status"><span class="status-dot active"></span> 系统运行正常</div><div class="footnote">
      <div style="margin-bottom:0.5rem;font-weight:600;color:#94a3b8">连接信息</div>
      数据库：<code>__DB_PATH__</code><br>
      仅限本机访问
    </div>
  </aside>
  <main>
    <div id="critical-banner" class="hidden" role="alert"></div>
    <div id="banner" class="banner" style="display: __BANNER_DISPLAY__;">__BANNER_MESSAGE__</div>

    <section id="overview" class="section active">
      <div class="page-header">
        <h2 data-kicker="I · Overview">总览</h2>
        <div class="meta" id="overview-meta">加载中…</div>
      </div>
      <div class="status-cards" id="status-cards"></div>
      <div class="cards" id="overview-cards"></div>
      <div class="panel">
        <h3>最近 12 篇作品</h3>
        <div id="overview-recent" class="empty">加载中…</div>
      </div>
      <div class="panel">
        <h3>系统提醒</h3>
        <div id="overview-warnings" class="empty">无警告</div>
      </div>
    </section>

    <section id="monitor" class="section">
      <div class="page-header">
        <h2 data-kicker="II · Monitor">监控面板</h2>
        <div class="actions">
          <label style="flex-direction:row; align-items:center; gap:0.6rem;">
            自动刷新
            <select id="monitor-refresh" style="width: auto;">
              <option value="0">关闭</option>
              <option value="15" selected>15s</option>
              <option value="30">30s</option>
              <option value="60">60s</option>
            </select>
          </label>
          <button class="ghost" id="btn-monitor-refresh">立即刷新</button>
        </div>
      </div>

      <div class="cards" id="monitor-kpis"></div>

      <div class="panel">
        <h3>预算与配额</h3>
        <div id="monitor-quota" class="empty">加载中…</div>
      </div>

      <div class="panel">
        <h3>每日消耗趋势 (最近 14 天)</h3>
        <div id="monitor-daily" class="empty">加载中…</div>
      </div>

      <div class="panel">
        <h3>最近事件流水</h3>
        <div id="monitor-events" class="empty">加载中…</div>
      </div>

      <div class="panel">
        <h3>错误与异常记录</h3>
        <div id="monitor-errors" class="empty">加载中…</div>
      </div>

      <div class="panel">
        <h3>系统健康状况</h3>
        <div id="monitor-health" class="empty">加载中…</div>
      </div>
    </section>

    <section id="generate" class="section">
      <div class="page-header">
        <h2 data-kicker="📝 短篇小说">创作工作台</h2>
        <div class="actions">
          <button class="btn-primary tiny" id="btn-console-run">🚀 立即执行任务</button>
          <button class="btn-danger tiny" id="btn-console-cancel" disabled>⏹ 取消</button>
        </div>
      </div>
      <div class="monitor-section" id="console-panel">
        <div class="monitor-header">
          <h3>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
            实时控制台
          </h3>
        </div>
        <div class="monitor-grid">
          <div class="monitor-card">
            <div class="monitor-icon-wrap">🔄</div>
            <div class="monitor-info">
              <div class="m-label">当前任务状态</div>
              <div class="m-value" id="console-current">空闲</div>
            </div>
          </div>
          <div class="monitor-card">
            <div class="monitor-icon-wrap">🛡️</div>
            <div class="monitor-info">
              <div class="m-label">番茄登录状态</div>
              <div class="m-value" id="console-login" style="color:#10b981">已登录</div>
            </div>
          </div>
          <div class="monitor-card">
            <div class="monitor-icon-wrap">📦</div>
            <div class="monitor-info">
              <div class="m-label">主题池数量</div>
              <div class="m-value" id="console-pool">128</div>
            </div>
          </div>
        </div>
      </div>

      <div class="panel" id="inbox-panel">
        <div class="inbox-head">
          <h3>📥 最近生成作品 <span class="meta" id="inbox-summary"></span></h3>
          <button class="ghost tiny" id="btn-inbox-refresh" type="button">🔄 刷新列表</button>
        </div>
        <div id="inbox-list" class="empty">加载中…</div>
      </div>

      <div class="panel" id="prompts-panel">
        <div class="inbox-head" style="cursor:pointer" id="prompts-panel-toggle">
          <h3>📝 提示词模板 <span class="meta" id="prompts-summary"></span></h3>
          <span style="font-size:0.8rem;color:var(--muted)" id="prompts-chevron">展开 ⌵</span>
        </div>
        <div id="prompts-body" style="display:none; margin-top:1rem">
          <div id="prompts-list" class="empty">加载中…</div>
        </div>
      </div>

      <div class="panel" id="progress-panel" style="display:none;">
        <h3>📋 实时生成进度 <span id="progress-story" class="meta"></span></h3>
        <div id="progress-retry-banner" class="retry-banner" style="display:none;"></div>
        <div id="progress-strip" class="phase-strip"></div>
        <div id="progress-section-sub" class="phase-section-sub"></div>
        <div class="phase-timeline-wrap">
          <div class="phase-timeline-head">⏱ 运行日志流水</div>
          <ol id="progress-timeline" class="phase-timeline"></ol>
        </div>
      </div>
    </section>

    <div id="artifact-modal" class="artifact-modal" style="display:none;">
      <div class="artifact-modal-inner">
        <div class="artifact-modal-head">
          <span id="artifact-modal-title">产物预览</span>
          <button class="ghost" id="artifact-modal-close" type="button">✕ 关闭</button>
        </div>
        <pre id="artifact-modal-body" class="artifact-modal-body">加载中…</pre>
      </div>
    </div>

    <div id="inbox-detail-modal" class="modal-backdrop">
      <div class="modal">
        <div class="modal-head" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem">
          <h3 id="inbox-detail-title" style="margin:0">作品详情</h3>
          <button class="ghost" id="inbox-detail-close" type="button">✕</button>
        </div>
        <div class="modal-body">
          <div class="modal-meta" id="inbox-detail-meta" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem;margin-bottom:1.5rem;padding:1rem;background:var(--panel-soft);border-radius:var(--radius-sm)"></div>
          <div id="inbox-detail-content">加载中…</div>
        </div>
        <div class="modal-actions" id="inbox-detail-foot"></div>
      </div>
    </div>

    <section id="long-novel" class="section">
      <!-- 书库视图（默认） -->
      <div id="ln-library-view">
        <div class="page-header">
          <h2 data-kicker="📖 长篇小说">书库</h2>
          <div class="actions">
            <button class="btn-primary" id="ln-btn-new-book">➕ 新建书籍</button>
            <button class="ghost tiny" id="ln-btn-refresh-books">🔄 刷新</button>
          </div>
        </div>
        <div id="ln-book-list" class="empty">加载中…</div>
      </div>

      <!-- 书籍工作区（选书后显示） -->
      <div id="ln-book-workspace" style="display:none">
        <div class="page-header">
          <h2 data-kicker="📖 长篇小说"><span id="ln-ws-book-title"></span></h2>
          <div class="actions">
            <button class="ghost tiny" id="ln-btn-back-library">← 返回书库</button>
            <span class="meta" id="ln-ws-progress"></span>
          </div>
        </div>
        <div style="display:flex;gap:0.5rem;margin-bottom:1rem">
          <button class="btn-primary tiny" data-ln-sub="writing">✍️ 写作</button>
          <button class="ghost tiny" data-ln-sub="overview">📊 概览</button>
        </div>
        <div id="ln-setup-strip" class="phase-strip" style="display:none;margin-bottom:1rem"></div>
        <div id="ln-setup-preview" class="panel" style="display:none;margin-bottom:1rem">
          <h4 id="ln-setup-preview-title">📄 步骤产出</h4>
          <pre id="ln-setup-preview-content" style="white-space:pre-wrap;font-size:0.85rem;max-height:400px;overflow:auto"></pre>
        </div>
        <div id="ln-writing-panel">
          <div style="display:flex;gap:0.5rem;margin-bottom:1rem">
            <button class="btn-primary" id="ln-btn-write-next">✍️ 写下一章</button>
            <button class="btn-warning tiny" id="ln-btn-rewrite">🔄 重写</button>
            <button class="ghost tiny" id="ln-btn-prev-chapter">◀ 上一章</button>
            <button class="ghost tiny" id="ln-btn-next-chapter">下一章 ▶</button>
          </div>
          <div id="ln-writing-context" class="panel" style="max-height:300px;overflow:auto;margin-bottom:1rem">
            <h4>📋 上下文</h4>
            <div id="ln-context-content" class="empty">选择章节后加载…</div>
          </div>
          <div id="ln-chapter-list" class="empty" style="max-height:300px;overflow:auto"></div>
          <div id="ln-writing-output" class="panel" style="display:none;margin-top:1rem">
            <h4 id="ln-output-title">📄 正文</h4>
            <pre id="ln-output-content" style="white-space:pre-wrap;font-size:0.85rem;max-height:500px;overflow:auto"></pre>
          </div>
        </div>
        <div id="ln-overview-panel" style="display:none">
          <div class="panel"><h4>📊 进度</h4><div id="ln-overview-progress"></div></div>
          <div class="panel"><h4>📝 章节</h4><div id="ln-overview-chapters" class="empty"></div></div>
        </div>
      </div>

      <div id="ln-new-book-modal" class="modal-backdrop" style="display:none">
        <div class="modal" style="max-width:650px">
          <h3>📖 新建长篇小说</h3>
          <div style="margin:0.5rem 0">
            <button class="btn-primary" id="ln-btn-ai-suggest">🤖 AI 推荐选题</button>
            <span class="meta" id="ln-suggest-status"></span>
          </div>
          <div id="ln-suggestions" style="display:none;margin:0.5rem 0;max-height:250px;overflow:auto"></div>
          <div style="display:flex;flex-direction:column;gap:0.75rem;margin:1rem 0">
            <label>书名 <input id="ln-new-title" placeholder="输入书名"></label>
            <label>题材 <input id="ln-new-genre" placeholder="如：玄幻/都市/仙侠"></label>
            <label>一句话梗概 <input id="ln-new-premise" placeholder="用一句话概括故事核心"></label>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem">
              <label>计划总章数 <input type="number" id="ln-new-chapters" value="30" min="10" max="2000"></label>
              <label>每章目标字数 <input type="number" id="ln-new-words" value="3000" min="1000" max="10000" step="500"></label>
            </div>
          </div>
          <div class="modal-actions">
            <button class="ghost" id="ln-new-book-cancel">取消</button>
            <button class="primary" id="ln-new-book-confirm">确认创建</button>
          </div>
        </div>
      </div>
    </section>

<section id="theme-pool" class="section">
      <div class="page-header">
        <h2 data-kicker="🎯 题材库">题材库</h2>
        <div class="actions">
          <button class="btn-primary tiny" id="tp-btn-import-all">📥 导入所有渠道</button>
          <button class="btn-primary tiny" id="tp-btn-fetch-fanqie">📡 拉取番茄榜单</button>
          <span class="meta" id="tp-status"></span>
        </div>
      </div>

      <div class="cards" style="grid-template-columns:repeat(4,1fr);margin-bottom:1rem" id="tp-stats"></div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem">
        <div class="panel">
          <h4>📡 数据来源渠道</h4>
          <div id="tp-sources" class="empty">加载中…</div>
        </div>
        <div class="panel">
          <h4>🔥 番茄实时热词 <span class="meta" id="tp-keywords-time"></span></h4>
          <div id="tp-keywords" class="empty">加载中…</div>
        </div>
      </div>

      <div class="panel" style="margin-bottom:1rem">
        <div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap;margin-bottom:0.75rem">
          <strong>筛选：</strong>
          <select id="tp-filter-type" style="width:auto"><option value="">全部类型</option><option value="short">短篇</option><option value="long">长篇</option></select>
          <select id="tp-filter-genre" style="width:auto"><option value="">全部分类</option></select>
          <select id="tp-filter-source" style="width:auto"><option value="">全部来源</option><option value="seeds">演化</option><option value="fanqie">番茄</option><option value="manual">手动</option></select>
          <label style="font-size:0.85rem;display:flex;align-items:center;gap:0.3rem;cursor:pointer"><input type="checkbox" id="tp-filter-unused"> 仅未使用</label>
          <span class="meta" id="tp-filter-count"></span>
        </div>
        <div id="tp-theme-list" class="empty" style="max-height:600px;overflow:auto">加载中…</div>
      </div>

      <!-- Category Trend Analysis -->
      <div class="panel" style="margin-bottom:1rem" id="tp-trend-panel">
        <h4>📈 AI 风评分析 · 分类热度排名</h4>
        <div style="font-size:0.78rem;color:var(--muted);margin-bottom:0.5rem">
          数据来源：FanqieRankTracker 每日新书榜 · 更新时间 <span id="tp-trend-time">—</span>
        </div>
        <div id="tp-trend-list" class="empty">加载中…</div>
      </div>
    </section>


    <section id="logs" class="section">
      <div class="page-header">
        <h2 data-kicker="III · Logs">系统日志</h2>
        <div class="actions">
          <label style="flex-direction:row; align-items:center; gap:0.6rem;">
            显示行数
            <input id="logs-lines" type="number" min="10" max="1000" value="150" style="width:100px;">
          </label>
          <button class="primary" id="btn-refresh-logs">刷新日志</button>
        </div>
      </div>
      <div class="panel">
        <p style="font-size:0.875rem;margin-bottom:1rem">日志路径：<code id="logs-file">未加载</code></p>
        <pre id="logs-output" class="log">正在获取系统实时日志...</pre>
      </div>
    </section>

    <section id="settings-edit" class="section settings-section">
      <div class="page-header">
        <h2 data-kicker="IV · Settings">设置</h2>
        <div class="meta" id="settings-meta">更改将立即生效并同步到本地配置文件。</div>
      </div>

      <details class="settings-group" id="grp-generation" open>
        <summary>✍️ AI 生成模型配置</summary>
        <div class="group-body">
          <div class="settings-row">
            <label for="gen-key">DeepSeek API Key</label>
            <div class="control">
              <div class="secret-input">
                <input type="password" id="gen-key" autocomplete="off" spellcheck="false">
                <button class="eye" type="button" data-eye="gen-key" title="显示/隐藏">👁</button>
              </div>
              <div class="control-inline">
                <button class="tiny primary" type="button" id="gen-test">🔍 测试连接</button>
                <span class="meta" id="gen-test-msg"></span>
              </div>
            </div>
          </div>
          <details class="settings-advanced">
            <summary>展开高级参数</summary>
            <div class="group-body">
              <div class="settings-row">
                <label for="gen-model">模型识别码 (Model ID)</label>
                <div class="control"><input type="text" id="gen-model" placeholder="deepseek-chat"></div>
              </div>
              <div class="settings-row">
                <label for="gen-base">API 代理地址 (Base URL)</label>
                <div class="control"><input type="text" id="gen-base" placeholder="https://api.deepseek.com"></div>
              </div>
              <div class="settings-row">
                <label for="gen-timeout">请求超时 (秒)</label>
                <div class="control"><input type="number" id="gen-timeout" min="5" max="900" step="5"></div>
              </div>
              <div class="settings-row">
                <label for="gen-retries">最大重试次数</label>
                <div class="control"><input type="number" id="gen-retries" min="0" max="20" step="1"></div>
              </div>
            </div>
          </details>
          <div class="group-actions">
            <button class="ghost" data-reset="generation">↩️ 还原修改</button>
            <button class="primary" data-save="generation">💾 保存本节设置</button>
          </div>
        </div>
      </details>

      <details class="settings-group" id="grp-system">
        <summary>💻 本地系统环境</summary>
        <div class="group-body">
          <div class="settings-row">
            <label>运行模式</label>
            <div class="control">
              <div style="display:flex;align-items:center;gap:1rem;padding:0.75rem 1rem;background:var(--panel);border-radius:var(--radius-sm);border:1px solid var(--border)" id="mode-toggle-row">
                <span id="mode-toggle-icon" style="font-size:1.25rem">⏳</span>
                <span id="mode-toggle-label" style="flex:1;font-weight:600">读取模式…</span>
                <button class="tiny" id="mode-switch-btn" type="button">🔄 切换模式</button>
              </div>
            </div>
          </div>
          <div class="settings-row">
            <label for="sys-autostart">Windows 自启动</label>
            <div class="control control-inline">
              <label class="toggle-switch">
                <input type="checkbox" id="sys-autostart">
                <span class="toggle-track"></span>
                <span>系统登录时自动运行 ANP<span style="font-size:0.7rem; color:var(--muted); font-weight:400; letter-spacing:0">AI 小说创作流水线</span></span>
              </label>
              <span class="meta" id="sys-autostart-hint"></span>
            </div>
          </div>
          <div class="settings-row">
            <label>运行数据与配额</label>
            <div class="control" style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
              <div class="form-group">
                <label for="sys-budget">月度预算上限 (CNY)</label>
                <input type="number" id="sys-budget" min="0" step="10">
              </div>
              <div class="form-group">
                <label for="sys-tokens">每日 Token 限额</label>
                <input type="number" id="sys-tokens" min="0" step="10000">
              </div>
            </div>
          </div>
          <div class="group-actions">
            <button class="primary" data-save="system">💾 保存设置</button>
          </div>
        </div>
      </details>
    </section>
  </main>
</div>

    



<div id="story-modal" class="modal-backdrop" role="dialog" aria-modal="true">
  <div class="modal">
    <div class="page-header">
      <h3 id="story-modal-title">作品详情</h3>
      <button class="ghost" id="story-modal-close">关闭</button>
    </div>
    <div id="story-modal-body" class="empty">加载中…</div>
  </div>
</div>

<div id="mode-confirm-modal"  class="modal-backdrop" role="dialog" aria-modal="true">
  <div class="modal">
    <div class="page-header">
      <h3>⚠️ 切换到真实模式</h3>
      <button class="ghost" id="mode-confirm-close">取消</button>
    </div>
    <p>真实模式会执行实际操作。请确认：</p>
    <div class="mode-confirm-list">
      <label><input type="checkbox" id="confirm-login"> 我已配置好番茄登录态</label>
      <label><input type="checkbox" id="confirm-consequences"> 我接受后果，我接受后果</label>
    </div>
    <div class="modal-actions">
      <button class="ghost" id="mode-confirm-cancel">取消</button>
      <button class="warning" id="mode-confirm-go" disabled>确认切换到真实模式</button>
    </div>
  </div>
</div>

<div id="toasts"></div>
"""


DASHBOARD_JS = r"""
(() => {


  const $ = (id) => document.getElementById(id);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const toasts = $('toasts');

  // ---------- Theme Management ----------
  function initTheme() {
    const savedTheme = localStorage.getItem('anp-theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    updateThemeUI(savedTheme);

    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
      toggle.addEventListener('click', () => {
        const current = document.documentElement.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('anp-theme', next);
        updateThemeUI(next);
      });
    }
  }

  function updateThemeUI(theme) {
    const icon = document.getElementById('theme-toggle-icon');
    const label = document.getElementById('theme-toggle-label');
    if (icon) icon.textContent = theme === 'dark' ? '☀️' : '🌙';
    if (label) label.textContent = theme === 'dark' ? '浅色模式' : '深色模式';
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
    card.innerHTML = '<div style="margin-bottom:1.5rem;font-size:3rem">❓</div>'
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

  async function loadCards() {
    try {
      const data = await api('/api/monitor/cards');
      const target = $('overview-cards');
      if (!target) return;
      const cards = [
        ['下次运行', (data.next_run || {}).label || '手动模式'],
        ['最近结果', (data.last_run || {}).message || '暂无记录'],
        ['登录状态', (data.login || {}).label || (data.login || {}).status || '未知'],
        ['预算', String((data.budget || {}).percent || 0) + '%'],
      ];
      target.innerHTML = cards.map(([label, value]) =>
        '<div class="card"><div class="label">' + escapeHtml(label) + '</div><div class="value">' + escapeHtml(value) + '</div></div>'
      ).join('');
    } catch (err) {
      const target = $('overview-cards');
      if (target) target.innerHTML = '';
    }
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

  async function loadMonitor() {
    try {
      const data = await api('/api/monitor');
      const kpis = $('monitor-kpis');
      if (kpis) {
        const total = (data.stories || {}).total || data.total || 0;
        const approved = (data.stories || {}).approved || data.approved || 0;
        const published = (data.stories || {}).published || data.published || 0;
        kpis.innerHTML =
          '<div class="card"><div class="label">全部作品</div><div class="value">' + escapeHtml(total) + '</div></div>' +
          '<div class="card"><div class="label">已批准</div><div class="value">' + escapeHtml(approved) + '</div></div>' +
          '<div class="card"><div class="label">已发布</div><div class="value">' + escapeHtml(published) + '</div></div>';
      }
      const quota = $('monitor-quota');
      if (quota) quota.textContent = '月预算使用：' + (((data.limits || {}).monthly_budget_used_pct) || 0) + '%';
      const health = $('monitor-health');
      if (health) health.textContent = '数据库：' + (((data.health || {}).db_path) || '未知');
      const events = $('monitor-events');
      if (events) events.textContent = '更新时间：' + (data.generated_at || '');
    } catch (err) {
      const kpis = $('monitor-kpis');
      if (kpis) kpis.innerHTML = '<div class="empty">加载失败：' + escapeHtml(err.message) + '</div>';
      toast(err.message, 'error');
    }
  }

  function bindMonitor() {
    const btn = $('btn-monitor-refresh');
    if (btn) btn.addEventListener('click', loadMonitor);
    const refresh = $('monitor-refresh');
    if (refresh) refresh.addEventListener('change', startMonitorTimer);
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
  function bindSettings() {}
  function loadAllSettings() {
    const meta = $('settings-meta');
    if (meta) meta.textContent = '设置面板已加载。';
  }

  function showSection(target) {
    $$('.section').forEach((el) => el.classList.toggle('active', el.id === target));
    $$('#nav button').forEach((btn) => btn.classList.toggle('active', btn.dataset.target === target));
    // Stop all timers, start only relevant ones
    if (_consoleTimer) { clearInterval(_consoleTimer); _consoleTimer = null; }
    if (monitorTimer) { clearInterval(monitorTimer); monitorTimer = null; }
    if (cardsTimer) { clearInterval(cardsTimer); cardsTimer = null; }
    if (notifSource) { notifSource.close(); notifSource = null; }
    if (target === 'overview') { loadOverview(); loadCards(); startCardsTimer(); }
    if (target === 'monitor') { loadMonitor(); startMonitorTimer(); }
    if (target === 'generate') { loadInbox(); startConsoleTimer(); }
    if (target === 'long-novel') { _lnActiveBookId = null; $('ln-library-view').style.display = ''; $('ln-book-workspace').style.display = 'none'; loadBookList(); }
    if (target === 'theme-pool') loadThemePoolPage();
    if (target === 'logs') loadLogs();
    if (target === 'settings-edit') loadAllSettings();
    if (target === 'overview' || target === 'monitor' || target === 'generate') startNotificationStream();
  }

  // ---------- Overview ----------
  async function loadOverview() {
    try {
      const data = await api('/api/dashboard');
      const stats = data.stats || data || {};
      const order = [
        ['total', '全部', 'ok'],
        ['pending', '待审核', 'warn'],
        ['needs_human', '转人工', 'warn'],
        ['approved', '已批准', 'ok'],
        ['published', '已发布', 'ok'],
        ['rejected', '已拒绝', ''],
        ['publish_paused', '发布暂停', 'warn'],
        ['failed', '失败', 'bad'],
      ];
      const statusTarget = $('status-cards');
      if (statusTarget) {
        statusTarget.innerHTML = `
          <div class="status-card">
            <div class="status-icon-box icon-all">📁</div>
            <div class="status-info">
              <div class="s-label">全部作品</div>
              <div class="s-value">${stats.total || 0}</div>
              <div class="s-sub">所有作品总数</div>
            </div>
          </div>
          <div class="status-card">
            <div class="status-icon-box icon-audit">🕒</div>
            <div class="status-info">
              <div class="s-label">待审核</div>
              <div class="s-value">${(stats.pending || 0) + (stats.needs_human || 0)}</div>
              <div class="s-sub">等待审核的作品</div>
            </div>
            <div class="mini-chart"><svg viewBox="0 0 60 25"><path d="M0 20 Q15 5, 30 18 T60 10" fill="none" stroke="#f59e0b" stroke-width="2"/></svg></div>
          </div>
          <div class="status-card">
            <div class="status-icon-box icon-approved">🛡️</div>
            <div class="status-info">
              <div class="s-label">已批准</div>
              <div class="s-value">${stats.approved || 0}</div>
              <div class="s-sub">已通过审核</div>
            </div>
            <div class="mini-chart"><svg viewBox="0 0 60 25"><path d="M0 22 Q15 20, 30 10 T60 5" fill="none" stroke="#10b981" stroke-width="2"/></svg></div>
          </div>
          <div class="status-card">
            <div class="status-icon-box icon-published">🚀</div>
            <div class="status-info">
              <div class="s-label">已发布</div>
              <div class="s-value">${stats.published || 0}</div>
              <div class="s-sub">已发布的作品</div>
            </div>
            <div class="mini-chart"><svg viewBox="0 0 60 25"><path d="M0 15 Q15 18, 30 8 T60 2" fill="none" stroke="#8b5cf6" stroke-width="2"/></svg></div>
          </div>
        `;
      }
      $('overview-cards').innerHTML = ''; // Clear old redundant card area

      $('overview-meta').textContent = '数据库：' + (data.database || '未知') + (data.dry_run ? '  ·  dry-run/mock 模式' : '  ·  live 模式');

      const recent = Array.isArray(data.recent) ? data.recent : [];
      $('overview-recent').classList.toggle('empty', recent.length === 0);
      if (recent.length === 0) {
        $('overview-recent').innerHTML = '当前没有作品。请先到”短篇小说”创建一篇。';
      } else {
        $('overview-recent').innerHTML = renderStoryTable(recent, { showActions: false });
      }

      const warnings = Array.isArray(data.warnings) ? data.warnings : [];
      if (warnings.length === 0) {
        $('overview-warnings').classList.add('empty');
        $('overview-warnings').textContent = '无警告';
      } else {
        $('overview-warnings').classList.remove('empty');
        $('overview-warnings').innerHTML = '<ul>' + warnings.map((w) => '<li>' + escapeHtml(w) + '</li>').join('') + '</ul>';
      }
    } catch (err) {
      $('overview-cards').innerHTML = '';
      $('overview-meta').textContent = '加载失败：' + err.message;
      toast(err.message, 'error');
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
      const dryRunMarker = story.is_dry_run ? '<span class="dryrun-marker" title="演练模式生成的作品，未真正发布">📋 演练</span>' : '';
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
        curEl.textContent = '#' + (cur.story_id || '?') + ' ' + phase;
        if (cancelBtn) cancelBtn.disabled = false;
        if (runBtn) runBtn.disabled = true;
        if (cur.story_id != null) ensureProgressTracking(Number(cur.story_id));
      } else {
        curEl.textContent = '空闲';
        if (cancelBtn) cancelBtn.disabled = true;
        if (runBtn) runBtn.disabled = false;
      }
      const login = data.login_state || {};
      const loginValid = login.status === 'valid' || login.status === 'expiring' || login.status === 'session_only' || login.status === 'cdp_active';
      const loginIconMap = {
        cdp_active: '✅',
        valid: '✅',
        expiring: '⚠️',
        session_only: '🟡',
      };
      const loginIcon = loginIconMap[login.status] || '🚫';
      const loginLabel = login.label || (loginValid ? '已连接' : '未登录');
      $('console-login').textContent = loginIcon + ' ' + loginLabel;
      $('console-pool').textContent = (data.theme_pool_count || 0) + ' 条';

      const banner = $('console-banner');
      if (banner) {
        const streak = Number(data.publish_fail_streak || 0);
        if (streak >= 3) {
          banner.style.display = 'block';
          banner.style.background = '#ffe5e5';
          banner.style.color = '#a40000';
          banner.style.padding = '8px 12px';
          banner.style.borderRadius = '4px';
          banner.style.marginTop = '8px';
          banner.textContent = '🚨 连续 3 次发布失败，请检查番茄登录态';
        } else if (!loginValid) {
          banner.style.display = 'block';
          banner.style.background = '#fff7e0';
          banner.style.color = '#a06000';
          banner.style.padding = '8px 12px';
          banner.style.borderRadius = '4px';
          banner.style.marginTop = '8px';
          banner.textContent = '⚠ 番茄登录态' + escapeHtml(loginLabel) + '，请到「设置 → 发布」重新登录后再发布';
        } else {
          banner.style.display = 'none';
        }
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
    banner.innerHTML = '🔁 这是 <strong>第 ' + retry.attempt + ' 次尝试</strong>' + tail
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
    if (status === 'in_progress') return '⏳';
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

  // ---------- Publish (发布管理页已下线；阶段 4 在生成进度卡片里复活) ----------;

  const INBOX_STATUS_LABELS = {
    pending: '待生成',
    needs_human: '需人工审核',
    approved: '已批准',
    published: '已发布',
    publish_paused: '发布暂停',
    publish_failed: '发布失败',
    failed: '失败',
    rejected: '已拒绝',
    cancelled: '已取消',
    paused_login_required: '登录待处理',
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
    // 已批准、发布失败、发布暂停 都可以触发发布（重试）
    if (status === 'approved' || status === 'publish_paused' || status === 'failed') {
      const label = status === 'approved' ? '🚀 立即发布' : '🔄 重试发布';
      html += '<button class="primary tiny" data-publish="' + sid + '">' + label + '</button>';
    }
    // 详情：查看每步骤结果
    html += '<button class="tiny ghost" data-detail="' + sid + '">📋 步骤</button>';
    // 删除：所有行都可以删
    html += '<button class="tiny danger" data-delete="' + sid + '">🗑 删除</button>';
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
        const pubBtn = event.target.closest('button[data-publish]');
        if (pubBtn) {
          event.stopPropagation();
          publishStory(pubBtn, Number(pubBtn.dataset.publish));
          return;
        }
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
          const icon = step.status === 'done' ? '✅' : step.status === 'running' ? '🔄' : step.status === 'failed' ? '❌' : step.status === 'skipped' ? '⏭️' : '⏸';
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
          '<button class="btn-primary tiny" id="phase-preview-prompt-btn" style="display:none">📝 提示词</button>' +
          '<button class="btn-warning tiny" id="phase-preview-rerun" style="display:none">🔄 重跑此步骤</button>' +
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
          phaseHtml += '<a href="' + href + '" target="_blank" class="inbox-badge" style="text-decoration:none;cursor:pointer" title="' + escapeHtml(String(a.size_bytes || '') + ' bytes') + '">📄 ' + escapeHtml(label) + '</a>';
        });
        phaseHtml += '</div>';
      }
      // timeline 折叠
      if (phases && Array.isArray(phases.timeline) && phases.timeline.length > 0) {
        phaseHtml += '<details style="margin-top:0.4rem; font-size:0.8rem"><summary>📅 阶段时间线（' + phases.timeline.length + ' 条）</summary>';
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

      // footer：删除 + 发布
      const canPublish = ['approved', 'publish_paused', 'failed'].indexOf(story.status) >= 0;
      footEl.innerHTML = '<button class="btn-ghost" id="inbox-detail-cancel">关闭</button>' +
        '<button class="btn-danger" id="inbox-detail-delete">🗑 删除</button>' +
        (canPublish
          ? '<button class="btn-primary" id="inbox-detail-publish">' +
            (story.status === 'approved' ? '🚀 立即发布' : '🔄 重试发布') +
            '</button>'
          : '');
      $('inbox-detail-cancel').addEventListener('click', () => modal.classList.remove('show'));
      $('inbox-detail-delete').addEventListener('click', () => {
        showConfirm('确定删除 #' + storyId + '？此操作不可撤销。', function() {
          modal.classList.remove('show');
          deleteStoryFromDetail(storyId);
        });
      });
      const pubBtn = $('inbox-detail-publish');
      if (pubBtn) {
        pubBtn.addEventListener('click', () => {
          modal.classList.remove('show');
          publishStory(pubBtn, story.id);
        });
      }

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
          titleEl.textContent = '📄 ' + escapeHtml(filename) + (existingFile && existingFile.size_bytes != null ? '  (' + existingFile.size_bytes + ' bytes)' : '');
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
      if (!confirm('确定恢复备份？')) return;
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
        var statusIcon = p.exists ? '✅' : '❌';
        var size = p.size_bytes != null ? ' (' + p.size_bytes + ' bytes)' : '';
        return '<div class="card-glass" style="display:flex;align-items:center;gap:0.75rem;padding:0.6rem 1rem;margin-bottom:0.4rem;cursor:pointer" data-prompt-phase="' + escapeHtml(p.phase) + '" data-prompt-label="' + escapeHtml(p.label) + '">'
          + '<span>' + statusIcon + '</span>'
          + '<span style="flex:1;font-weight:600">' + escapeHtml(p.label) + '</span>'
          + '<span class="inbox-meta" style="font-size:0.75rem">' + escapeHtml(p.filename || '') + size + '</span>'
          + '<button class="btn-primary tiny" data-prompt-open="' + escapeHtml(p.phase) + '">📝 编辑</button>'
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

  function bindLongNovel() {
    $('ln-btn-new-book').addEventListener('click', function() { $('ln-new-book-modal').style.display = 'flex'; });
    $('ln-new-book-cancel').addEventListener('click', function() { $('ln-new-book-modal').style.display = 'none'; });
    $('ln-new-book-confirm').addEventListener('click', createNewBook);
    $('ln-btn-ai-suggest').addEventListener('click', aiSuggestBooks);
    $('ln-btn-refresh-books').addEventListener('click', loadBookList);
    $('ln-btn-back-library').addEventListener('click', function() {
      _lnActiveBookId = null;
      $('ln-library-view').style.display = '';
      $('ln-book-workspace').style.display = 'none';
      loadBookList();
    });
    // Sub-tabs
    $$('[data-ln-sub]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        $$('[data-ln-sub]').forEach(function(b) { b.className = 'ghost tiny'; });
        btn.className = 'btn-primary tiny';
        var sub = btn.dataset.lnSub;
        $('ln-writing-panel').style.display = (sub === 'writing') ? '' : 'none';
        $('ln-overview-panel').style.display = (sub === 'overview') ? '' : 'none';
        if (sub === 'overview') loadBookOverview();
      });
    });
    $('ln-btn-write-next').addEventListener('click', writeNextChapter);
    $('ln-btn-rewrite').addEventListener('click', rewriteCurrentChapter);
    $('ln-btn-prev-chapter').addEventListener('click', function() { navigateChapter(-1); });
    $('ln-btn-next-chapter').addEventListener('click', function() { navigateChapter(1); });
    // L0 phase chip clicks
    var setupStrip = document.getElementById('ln-setup-strip');
    if (setupStrip) setupStrip.addEventListener('click', async function(e) {
      var chip = e.target.closest('[data-ln-phase]');
      if (!chip || chip.dataset.lnPhase === 'done' || chip.dataset.lnPhase === 'finalize') return;
      var fileMap = {'premise':['设定/题材定位.md','题材定位'],'world':['设定/世界观/背景设定.md','世界观'],'characters':['设定/角色/角色设定.md','角色设计'],'outline':['大纲/大纲.md','大纲']};
      var info = fileMap[chip.dataset.lnPhase]; if (!info) return;
      var relPath = info[0], label = info[1];
      var previewEl = document.getElementById('ln-setup-preview'), titleEl = document.getElementById('ln-setup-preview-title'), contentEl = document.getElementById('ln-setup-preview-content');
      try {
        var resp = await fetch('/api/long-novel/books/' + _lnActiveBookId + '/artifact?path=' + encodeURIComponent(relPath));
        if (resp.ok) { var data = await resp.json(); if (previewEl) previewEl.style.display = ''; if (titleEl) titleEl.textContent = '📄 ' + label; if (contentEl) contentEl[data.is_dir?'innerHTML':'textContent'] = data.is_dir ? (data.files||[]).map(function(f){return '<span style="cursor:pointer;color:var(--primary);text-decoration:underline" onclick="_lnLoadFile("'+escapeHtml(relPath+'/'+f.name)+'")">📄 '+escapeHtml(f.name)+'</span> ('+f.size+' bytes)<br>';}).join('') : (data.content||'').substring(0,15000); }
      } catch(_e){ if(previewEl)previewEl.style.display=''; if(titleEl)titleEl.textContent=label+'（加载失败）'; }
    });
  }  // ── 书库 ──
  function _lnOpenBook(bookId) {
    _lnActiveBookId = bookId;
    $('ln-library-view').style.display = 'none';
    $('ln-book-workspace').style.display = '';
    loadWritingWorkbench();
  }

  async function loadBookList() {
    var list = $('ln-book-list');
    if (!list) return;
    try {
      var data = await api('/api/long-novel/books');
      var books = data.books || [];
      var countEl = $('ln-library-count'); if (countEl) countEl.textContent = books.length;
      if (books.length === 0) {
        list.innerHTML = '<div class="inbox-meta">暂无书籍，点击"新建书籍"开始</div>';
        return;
      }
      list.innerHTML = books.map(function(b) {
        var statusLabel = {setup:'📋 设定中', writing:'✍️ 连载中', completed:'✅ 已完成', paused:'⏸ 暂停'}[b.status] || b.status;
        return '<div class="card-glass" style="display:flex;align-items:center;gap:1rem;padding:0.75rem 1rem;margin-bottom:0.5rem;cursor:pointer" data-ln-book-id="' + b.id + '">'
          + '<span style="font-size:1.5rem">📖</span>'
          + '<span style="flex:1"><strong>' + escapeHtml(b.title) + '</strong>'
          + '<br><span class="inbox-meta">' + escapeHtml(b.genre || '未分类') + ' · ' + (b.current_chapter || 0) + '/' + (b.target_chapters || 30) + ' 章</span></span>'
          + '<span class="badge-indigo">' + statusLabel + '</span>'
          + '<button class="btn-danger tiny" data-ln-del="' + b.id + '">✕</button>'
          + '</div>';
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
      });
    } catch (err) { list.innerHTML = '<div class="inbox-meta" style="color:var(--danger)">加载失败：' + err.message + '</div>'; }
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
      _lnActiveBookId = data.book_id;
      _lnActiveTab = 'setup';
      refreshLongNovelTab();
    } catch (err) { toast('创建失败：' + err.message, 'error'); }
  }

  // ── 创作准备 ──
  async function showSetupPanel() {
    if (!_lnActiveBookId) return;
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = data.book || {};
      $('ln-setup-book-title').textContent = '📖 ' + escapeHtml(book.title);
      $('ln-setup-progress').textContent = escapeHtml(book.genre || '') + ' · ' + (book.target_chapters || 30) + '章计划';
      // Auto-trigger setup
    } catch (_e) {}
  }


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
            statusEl.textContent = ' ✅ 已选择：' + s.title;
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

  async function loadWritingWorkbench() {
    if (!_lnActiveBookId) {
      $('ln-ws-book-title').textContent = '请先在书库中选择一本书';
      return;
    }
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = data.book || {};
      $('ln-ws-book-title').textContent = '📖 ' + escapeHtml(book.title);
      $('ln-ws-progress').textContent = '第' + (book.current_chapter || 0) + '/' + (book.target_chapters || 30) + '章 · ' + escapeHtml(book.genre || '');
      $('ln-ws-progress').textContent = book.title;

      // Load chapters
      var chData = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters');
      var chapters = chData.chapters || [];
      var list = $('ln-chapter-list');
      if (chapters.length === 0) {
        list.innerHTML = '<div class="inbox-meta">暂无章节，请先点击"写下一章"开始</div>';
      } else {
        list.innerHTML = '<div style="font-weight:600;margin-bottom:0.3rem">章节列表</div>'
          + chapters.map(function(c) {
              var icon = {outline_only:'⏸', writing:'🔄', draft:'📝', reviewed:'✅', published:'🚀'}[c.status] || '⏸';
              var sel = (c.chapter_number === _lnViewChapter) ? ' style="background:var(--primary-soft)"' : '';
              return '<div class="card-glass" data-ln-ch="' + c.chapter_number + '"' + sel + ' style="display:flex;align-items:center;gap:0.5rem;padding:0.4rem 0.75rem;margin-bottom:0.2rem;cursor:pointer;font-size:0.85rem">'
                + '<span>' + icon + '</span>'
                + '<span style="flex:1">第' + c.chapter_number + '章 ' + escapeHtml(c.title || '') + '</span>'
                + '<span class="inbox-meta" style="font-size:0.7rem">' + (c.actual_words || 0) + '字</span>'
                + '</div>';
            }).join('');
        list.querySelectorAll('[data-ln-ch]').forEach(function(row) {
          row.addEventListener('click', function() {
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
        $('ln-output-title').textContent = '📄 第' + chNum + '章 ' + escapeHtml(ch.title || '');
        $('ln-output-content').textContent = ch.content;
      } else {
        $('ln-writing-output').style.display = 'none';
      }
      // Load context
      var ctxData = await api('/api/long-novel/books/' + _lnActiveBookId + '/context/' + chNum);
      var ctx = ctxData.context || {};
      var ctxHtml = '';
      if (ctx.outline) ctxHtml += '<div style="margin-bottom:0.5rem"><strong>章纲：</strong>' + escapeHtml(ctx.outline.substring(0, 500)) + '</div>';
      if (ctx.prev_chapter_summary) ctxHtml += '<div style="margin-bottom:0.5rem"><strong>上章摘要：</strong>' + escapeHtml(ctx.prev_chapter_summary) + '</div>';
      if (ctx.foreshadowing) ctxHtml += '<div style="margin-bottom:0.5rem"><strong>相关伏笔：</strong><pre style="font-size:0.75rem;white-space:pre-wrap">' + escapeHtml(ctx.foreshadowing.substring(0, 1000)) + '</pre></div>';
      if (!ctxHtml) ctxHtml = '<span class="inbox-meta">暂无上下文，请先完成开书设定</span>';
      $('ln-context-content').innerHTML = ctxHtml;
    } catch (err) { /* silent */ }
  }

  async function writeNextChapter() {
    if (!_lnActiveBookId) { toast('请先选择一本书', 'error'); return; }
    // Check if book needs setup first
    try {
      var bkData = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = bkData.book || {};
      if (book.status === 'setup') {
        if (!confirm('这本书尚未完成开书设定。是否现在运行？\n\n将依次执行：\n📌 题材定位\n🌍 世界观\n👤 角色设计\n📋 大纲+30章细纲\n\n每个步骤完成后会展示生成的预览内容。')) return;
        var release = withBusy($('ln-btn-write-next'), '启动中…');
        release();
        _lnRunSetupPhases();
        return;
      }
      // Get next chapter
      var nextData = await api('/api/long-novel/books/' + _lnActiveBookId + '/next-chapter');
      if (!nextData.chapter) { toast('所有章节已完成！', 'info'); return; }
      var chNum = nextData.chapter.chapter_number;
      if (!confirm('即将自动撰写第' + chNum + '章（约' + (book.target_words_per_chapter || 3000) + '字），包括：\n\n1. 上下文装配\n2. AI生成初稿\n3. 扩写补字\n4. 精修润色\n5. 去AI味\n6. 连续性检查\n7. 4维审查\n\n确认开始？')) return;
      var release = withBusy($('ln-btn-write-next'), '写作中…');
      try {
        var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/write-chapter/' + chNum, { method: 'POST' });
        var r = result.result || {};
        var review = r.review || {};
        toast('第' + chNum + '章完成！' + r.final_words + '字 · 审查：' + (review.overall || '?'), 'success');
        _lnViewChapter = chNum;
        loadWritingWorkbench();
      } catch (err) { toast('写作失败：' + err.message, 'error'); }
      finally { release(); }
    } catch (err) { toast('错误：' + err.message, 'error'); }
  }

  var _lnSetupPollTimer = null;

  async function _lnRunSetupPhases() {
    var stripEl = document.getElementById('ln-setup-strip');
    var previewEl = document.getElementById('ln-setup-preview');
    var titleEl = document.getElementById('ln-setup-preview-title');
    var contentEl = document.getElementById('ln-setup-preview-content');

    var phases = [
      {id: 'premise', icon: '📌', label: '题材定位'},
      {id: 'world', icon: '🌍', label: '世界观'},
      {id: 'characters', icon: '👤', label: '角色设计'},
      {id: 'outline', icon: '📋', label: '大纲+细纲'}
    ];

    // Build strip
    if (stripEl) {
      stripEl.style.display = 'flex';
      stripEl.innerHTML = phases.map(function(ph) {
        return '<div class="phase-chip" data-ln-phase="' + ph.id + '" style="cursor:pointer" title="点击查看生成内容">'
          + '<span class="phase-chip-icon">⏸</span>'
          + '<span class="phase-chip-label">' + ph.icon + ' ' + ph.label + '</span></div>';
      }).join('');
    }
    if (previewEl) previewEl.style.display = '';
    if (contentEl) contentEl.textContent = '准备开始...（每个步骤需要30-90秒，请耐心等待）';

    for (var i = 0; i < phases.length; i++) {
      var ph = phases[i];
      _lnUpdateSetupChip(stripEl, ph.id, '🔄');
      if (titleEl) titleEl.textContent = '🔄 ' + ph.icon + ' ' + ph.label + ' — AI生成中...';
      if (contentEl) contentEl.textContent = '正在调用AI，请耐心等待（通常30-90秒）...';

      // Start the phase (returns immediately)
      try {
        await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/' + ph.id, { method: 'POST', body: {} });
      } catch (err) {
        _lnUpdateSetupChip(stripEl, ph.id, '❌');
        if (contentEl) contentEl.textContent = '启动失败：' + (err.message || '未知错误');
        return;
      }

      // Poll until done
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
            _lnUpdateSetupChip(stripEl, ph.id, '✅');
            if (titleEl) titleEl.textContent = '✅ ' + ph.icon + ' ' + ph.label + ' — 完成';
            // Show preview
            var preview = sData.detail || '';
            if (preview && preview.length > 20) {
              if (contentEl) contentEl.textContent = preview.substring(0, 5000);
            }
          } else if (st === 'error') {
            done = true;
            _lnUpdateSetupChip(stripEl, ph.id, '❌');
            if (titleEl) titleEl.textContent = '❌ ' + ph.label + ' — 失败';
            if (contentEl) contentEl.textContent = '错误：' + (sData.detail || '未知');
            toast(ph.label + '失败', 'error');
            return;
          }
        } catch (_e) {
          if (contentEl) contentEl.textContent = '轮询出错，重试中...';
        }
      }
      // Brief pause between phases
      await new Promise(function(r) { setTimeout(r, 600); });
    }

    // Finalize
    try {
      _lnUpdateSetupChip(stripEl, 'outline', '✅');
      if (contentEl) contentEl.textContent = '正在写入数据库...';
      await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/finalize', { method: 'POST', body: {} });
      // Poll finalize
      var fDone = false;
      while (!fDone) {
        await new Promise(function(r) { setTimeout(r, 1500); });
        var fs = await api('/api/long-novel/books/' + _lnActiveBookId + '/setup-phase/finalize/status');
        if (fs.status === 'done') { fDone = true; }
      }
      if (titleEl) titleEl.textContent = '✅ 开书设定全部完成！共30章细纲';
      if (contentEl) contentEl.textContent = '可以开始逐章写作了。';
      toast('开书设定完成！', 'success');
      setTimeout(function() { loadWritingWorkbench(); }, 1500);
    } catch (err) {
      if (contentEl) contentEl.textContent = '收尾失败：' + err.message;
    }
  }

  async function _lnLoadFile(relPath) {
    var previewEl = document.getElementById('ln-setup-preview');
    var titleEl = document.getElementById('ln-setup-preview-title');
    var contentEl = document.getElementById('ln-setup-preview-content');
    try {
      var resp = await fetch('/api/long-novel/books/' + _lnActiveBookId + '/artifact?path=' + encodeURIComponent(relPath));
      if (resp.ok) {
        var data = await resp.json();
        if (titleEl) titleEl.textContent = '📄 ' + relPath;
        if (contentEl) contentEl.textContent = (data.content || '').substring(0, 15000);
      }
    } catch (_e) { if (contentEl) contentEl.textContent = '加载失败'; }
  }

  function _lnUpdateSetupChip(stripEl, phaseId, icon) {
    if (!stripEl) return;
    var chip = stripEl.querySelector('[data-ln-phase="' + phaseId + '"]');
    if (!chip) return;
    var iconEl = chip.querySelector('.phase-chip-icon');
    if (iconEl) iconEl.textContent = icon;
    if (icon === '🔄') chip.style.outline = '2px solid var(--primary)';
    else if (icon === '❌') chip.style.outline = '2px solid var(--danger)';
    else chip.style.outline = '';
  }

  function _lnPollSetupProgress() {
    if (_lnSetupPollTimer) clearInterval(_lnSetupPollTimer);
    _lnSetupPollTimer = setInterval(function() {}, 2000);
  }

  async function rewriteCurrentChapter() {
    if (!_lnActiveBookId || !_lnViewChapter) { toast('请先选择章节', 'error'); return; }
    if (!confirm('确定重写第' + _lnViewChapter + '章？将自动备份原稿并检查后续章节连续性。')) return;
    var release = withBusy($('ln-btn-rewrite'), '重写中…');
    try {
      var result = await api('/api/long-novel/books/' + _lnActiveBookId + '/rewrite-chapter/' + _lnViewChapter, { method: 'POST' });
      toast('第' + _lnViewChapter + '章已重写' + (result.cascade_affected ? '，' + result.cascade_affected + '个后续章节可能需要检查' : ''), 'success');
      loadWritingWorkbench();
    } catch (err) { toast('重写失败：' + err.message, 'error'); }
    finally { release(); }
  }

  // ── 概览 ──
  async function loadBookOverview() {
    if (!_lnActiveBookId) {
      $('ln-overview-status').textContent = '—';
      $('ln-overview-progress').innerHTML = '<span class="inbox-meta">请先在书库中选择一本书</span>';
      $('ln-overview-chapters').innerHTML = '';
      return;
    }
    try {
      var data = await api('/api/long-novel/books/' + _lnActiveBookId);
      var book = data.book || {};
      $('ln-overview-status').textContent = book.title;
      var done = book.completed_chapters || 0;
      var total = book.target_chapters || 30;
      var pct = total > 0 ? Math.round(done / total * 100) : 0;
      $('ln-overview-progress').innerHTML =
        '<div style="display:flex;align-items:center;gap:1rem">'
        + '<div style="flex:1;background:var(--panel-soft);border-radius:8px;height:12px;overflow:hidden">'
        + '<div style="background:var(--primary);height:100%;width:' + pct + '%"></div></div>'
        + '<span style="font-weight:600">' + done + '/' + total + ' 章 (' + pct + '%)</span></div>'
        + '<div class="inbox-meta" style="margin-top:0.5rem">总字数：' + (book.total_words || 0) + ' · 题材：' + escapeHtml(book.genre || '未设置') + '</div>';
      // Chapters
      var chData = await api('/api/long-novel/books/' + _lnActiveBookId + '/chapters');
      var chapters = chData.chapters || [];
      $('ln-overview-chapters').innerHTML = chapters.length === 0
        ? '<span class="inbox-meta">暂无章节</span>'
        : chapters.map(function(c) {
            var icon = c.status === 'published' ? '✅' : c.status === 'draft' ? '📝' : c.status === 'reviewed' ? '🔍' : '⏸';
            return '<span style="display:inline-block;margin:0.15rem;padding:0.15rem 0.4rem;background:var(--panel-soft);border-radius:3px;font-size:0.75rem;cursor:pointer" title="第' + c.chapter_number + '章 ' + escapeHtml(c.title || '') + '">' + icon + ' ' + c.chapter_number + '</span>';
          }).join('');
    } catch (err) { toast('加载概览失败：' + err.message, 'error'); }
  }


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
        '<div class="status-card"><div class="status-icon-box" style="background:var(--primary-soft)">📦</div><div>' + (s.total || 0) + '</div><div class="inbox-meta">总题材</div></div>' +
        '<div class="status-card"><div class="status-icon-box" style="background:var(--success-soft)">🆕</div><div>' + (s.unconsumed || 0) + '</div><div class="inbox-meta">未使用</div></div>' +
        '<div class="status-card"><div class="status-icon-box" style="background:var(--warning-soft)">📡</div><div>' + (s.sources || []).length + '</div><div class="inbox-meta">来源渠道</div></div>' +
        '<div class="status-card"><div class="status-icon-box" style="background:var(--danger-soft)">📋</div><div>' + typesHtml + '</div><div class="inbox-meta">类型分布</div></div>';

      // Sources
      var srcData = await api("/api/themes/trending/sources");
      var sources = srcData.sources || [];
      var sourceCounts = {};
      (s.sources || []).forEach(function(sc) { sourceCounts[sc.source] = sc.count; });
      var srcEl = document.getElementById("tp-sources");
      if (srcEl) srcEl.innerHTML = sources.map(function(src) {
        var count = sourceCounts[src.id] || 0;
        var lastFetch = "";
        (s.sources || []).forEach(function(sc) {
          if (sc.source === src.id && sc.last_fetch) lastFetch = " · 最后更新: " + sc.last_fetch;
        });
        return '<div style="padding:0.5rem;background:var(--panel-soft);border-radius:4px;margin-bottom:0.4rem;font-size:0.85rem">'
          + '<strong>' + src.icon + ' ' + src.name + '</strong> <span class="badge-indigo">' + count + '条</span>'
          + '<br><span class="inbox-meta">' + src.desc + '</span>'
          + '<br><span class="inbox-meta">频率: ' + src.frequency + lastFetch + '</span>'
          + (src.url ? '<br><span class="inbox-meta" style="word-break:break-all;font-size:0.7rem">' + src.url + '</span>' : '')
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
            var medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : '';
            var hotLabel = c.hotness_score > 60 ? '🔥' : c.hotness_score > 30 ? '📈' : '📊';
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

    var sourceIcons = {seeds: "📋", fanqie: "📡", manual: "✏️", history: "📊"};
    list.innerHTML = themes.map(function(t) {
      var typeLabel = t.target_type === "long" ? "长篇" : "短篇";
      var consumedIcon = t.is_consumed ? "✅" : "🆕";
      var srcIcon = sourceIcons[t.source] || "❓";
      var words = t.target_words_min ? (t.target_words_min + "-" + t.target_words_max + "字") : "";
      var timeInfo = t.fetched_at || t.created_at || "";
      if (timeInfo.length > 10) timeInfo = timeInfo.substring(0, 10);
      return '<div class="card-glass" style="padding:0.6rem 0.8rem;margin-bottom:0.3rem;font-size:0.85rem;cursor:pointer" data-tp-id="' + t.id + '">'
        + '<div style="display:flex;align-items:center;gap:0.5rem">'
        + '<span>' + consumedIcon + '</span>'
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
      var sourceIcons = {seeds: "📋 演化", fanqie: "📡 番茄榜单", manual: "✏️ 手动", history: "📊 历史"};
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

  var _initDone = false;

  function init() {
    if (_initDone) return;
    _initDone = true;
    // Nav bindings (always)
    $$('#nav button').forEach((btn) => btn.addEventListener('click', () => showSection(btn.dataset.target)));
    // Deferred bindings (fast, no API calls)
    bindArtifactModal();
    bindConsole();
    bindReview();
    bindInbox();
    bindLogs();
    bindMonitor();
    bindPromptsPanel();
    bindLongNovel();
    bindThemePoolPage();
    bindModeToggle();
    bindSettings();
    initTheme();
    // Only start timers for visible section (overview is default)
    loadOverview();
    loadCards();
    startCardsTimer();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
"""


__all__ = ["DASHBOARD_BODY_TEMPLATE", "DASHBOARD_CSS", "DASHBOARD_JS"]
