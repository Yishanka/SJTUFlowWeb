const routes = [
  { id: "dashboard", label: "控制面板", icon: "D" },
  { id: "chat", label: "学习对话", icon: "T" },
  { id: "skills", label: "技能库", icon: "S" },
  { id: "transcripts", label: "转写库", icon: "R" },
  { id: "media", label: "媒体转写", icon: "M" },
  { id: "settings", label: "本地设置", icon: "C" },
];

const defaultApiBase = (() => {
  if (location.protocol === "file:" || location.port === "5173") {
    return "http://127.0.0.1:8765";
  }
  return "";
})();

const state = {
  route: localStorage.getItem("sjtuflow.route") || "dashboard",
  apiBase: localStorage.getItem("sjtuflow.apiBase") || defaultApiBase,
  loading: new Set(),
  notices: [],
  config: null,
  doctor: null,
  briefing: null,
  sessions: [],
  activeSession: null,
  skills: [],
  selectedSkill: null,
  skillDraft: null,
  transcripts: [],
  transcriptQuery: "",
  selectedTranscript: null,
  jobs: [],
};

const app = document.querySelector("#app");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function compact(value, fallback = "Not set") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function displayPath(value) {
  const text = compact(value, "");
  if (!text) return "";
  return text.replace(/^\/Users\/[^/]+/, "~");
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function truncate(value, length = 140) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > length ? `${text.slice(0, length - 1)}...` : text;
}

function skillSourceLabel(source) {
  if (source === "builtin") return "内置";
  if (source === "user") return "我的";
  return source || "技能";
}

const skillGuides = {
  "announcement-triage": {
    purpose: "帮你快速判断课程公告里哪些内容需要马上处理，哪些只是资料更新或普通通知。",
    timing: "适合在一周内公告很多、担心漏掉报名、确认、投票、DDL 等动作时使用。",
    output: "会整理出需要行动的公告清单，并标出课程、发布时间、建议动作和紧急程度。",
  },
  "assignment-planning": {
    purpose: "把一份作业要求拆成可以执行的步骤，避免一上来就不知道从哪里开始。",
    timing: "适合刚拿到作业说明、准备开工前使用。",
    output: "会给出任务拆解、资料清单、时间安排和提交前检查项。",
  },
  "course-briefing": {
    purpose: "按课程汇总最近发生的事情，让你先看一份简短课程动态。",
    timing: "适合几天没看 Canvas，或者想知道某门课最近有没有新公告、作业和资料时使用。",
    output: "会输出课程近况、重要更新、待办事项和需要继续查看的材料。",
  },
  "final-review": {
    purpose: "把课程转写、作业和公告串起来，生成更适合复习周使用的计划。",
    timing: "适合期末前整理一门课的复习范围和优先级。",
    output: "会生成复习主题、时间安排、重点材料和下一步行动建议。",
  },
  "lecture-capture": {
    purpose: "在转写课程录像前帮你判断是否值得转写，以及是否已经有类似内容。",
    timing: "适合准备把本地视频或 Canvas 录播转成 transcript 前使用。",
    output: "会给出建议转写、跳过或需要先检查的判断，降低重复转写的成本。",
  },
  "study-qa": {
    purpose: "根据已有课堂转写回答具体学习问题，并尽量引用原文依据。",
    timing: "适合你想问某节课讲了什么、老师怎么解释某个概念、某个内容是否出现过时使用。",
    output: "会给出带依据的回答；如果转写稿里没有相关内容，也会明确说明。",
  },
  "transcript-review": {
    purpose: "把一段课堂转写整理成课后复盘摘要。",
    timing: "适合刚完成转写后，想快速知道这节课重点、考点和提醒事项时使用。",
    output: "会提炼主题、关键时间点、作业提醒和需要回看的片段。",
  },
  "weekly-review": {
    purpose: "把最近课程动态和未来截止事项合成一份周报。",
    timing: "适合每周开始或周末整理学习安排时使用。",
    output: "会生成本周重点、即将截止的任务和需要优先处理的事项。",
  },
};

function skillGuide(skill) {
  const key = String(skill?.name || "").toLowerCase();
  return (
    skillGuides[key] || {
      purpose: skill?.description || "这个技能会按预设流程读取相关学习资料，并帮助你整理成更容易行动的结果。",
      timing: "适合在你已经有课程资料、公告、作业或转写内容，但还需要进一步整理时使用。",
      output: "会根据当前技能的设置输出摘要、计划、清单或问答结果。",
    }
  );
}

function apiUrl(path) {
  return `${state.apiBase}${path}`;
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(apiUrl(path), { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const message = payload?.detail || payload?.message || response.statusText;
    throw new Error(message);
  }
  return payload;
}

function setLoading(key, value) {
  if (value) state.loading.add(key);
  else state.loading.delete(key);
  render();
}

function isLoading(key) {
  return state.loading.has(key);
}

function notify(message, type = "info") {
  const id = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  state.notices.push({ id, message, type });
  render();
  window.setTimeout(() => {
    state.notices = state.notices.filter((item) => item.id !== id);
    render();
  }, 4200);
}

async function loadResource(key, loader) {
  setLoading(key, true);
  try {
    await loader();
  } catch (error) {
    notify(error.message || String(error), "error");
  } finally {
    setLoading(key, false);
  }
}

async function refreshCore() {
  await Promise.allSettled([
    loadConfig(false),
    loadDoctor(false),
    loadBriefing(false),
    loadSessions(false),
    loadSkills(false),
    loadTranscripts(false),
    loadJobs(false),
  ]);
  render();
}

async function loadConfig(withRender = true) {
  if (withRender) setLoading("config", true);
  try {
    state.config = await api("/api/config");
  } finally {
    if (withRender) setLoading("config", false);
  }
}

async function loadDoctor(withRender = true) {
  if (withRender) setLoading("doctor", true);
  try {
    state.doctor = await api("/api/doctor");
  } finally {
    if (withRender) setLoading("doctor", false);
  }
}

async function loadBriefing(withRender = true) {
  if (withRender) setLoading("briefing", true);
  try {
    state.briefing = await api("/api/briefing");
  } finally {
    if (withRender) setLoading("briefing", false);
  }
}

async function loadSessions(withRender = true) {
  if (withRender) setLoading("sessions", true);
  try {
    state.sessions = await api("/api/sessions");
  } finally {
    if (withRender) setLoading("sessions", false);
  }
}

async function loadSkills(withRender = true) {
  if (withRender) setLoading("skills", true);
  try {
    state.skills = await api("/api/skills");
  } finally {
    if (withRender) setLoading("skills", false);
  }
}

async function loadTranscripts(withRender = true) {
  if (withRender) setLoading("transcripts", true);
  try {
    const query = state.transcriptQuery.trim();
    state.transcripts = query
      ? await api(`/api/transcripts/search?q=${encodeURIComponent(query)}&limit=20`)
      : await api("/api/transcripts");
  } finally {
    if (withRender) setLoading("transcripts", false);
  }
}

async function loadJobs(withRender = true) {
  if (withRender) setLoading("jobs", true);
  try {
    state.jobs = await api("/api/jobs");
  } finally {
    if (withRender) setLoading("jobs", false);
  }
}

function setRoute(route) {
  state.route = route;
  localStorage.setItem("sjtuflow.route", route);
  render();
  if (route === "dashboard") loadBriefing();
  if (route === "chat") loadSessions();
  if (route === "skills") loadSkills();
  if (route === "transcripts") loadTranscripts();
  if (route === "media") loadJobs();
  if (route === "settings") {
    loadConfig();
    loadDoctor();
  }
}

function topbar() {
  const copy = {
    dashboard: ["SJTUFlow 学习工作台", "整合 Canvas、会话、Skills 与课堂转写的本地学习助手。"],
    chat: ["学习对话", "继续历史会话，或直接询问作业、公告、资料与转写内容。"],
    skills: ["技能库", "查看内置学习流程，也可以复制后创建自己的本地技能。"],
    transcripts: ["课堂转写库", "浏览、搜索和维护本地转写资料，点击后再按需读取全文。"],
    media: ["媒体转写任务", "将本地音视频或已授权媒体流转换为课堂转写稿。"],
    settings: ["本地设置", "配置模型、Canvas、资料目录与安全确认策略。"],
  }[state.route] || ["SJTUFlow", "本地优先的学习助手。"];

  return `
    <header class="topbar">
      <div>
        <h1>${escapeHtml(copy[0])}</h1>
        <p>${escapeHtml(copy[1])}</p>
      </div>
      <div class="top-actions">${topbarActions()}</div>
    </header>
  `;
}

function topbarActions() {
  if (state.route === "dashboard") {
    return `
      <button class="button" data-action="refresh-core">刷新</button>
      <button class="button primary" data-route="chat">开始对话</button>
    `;
  }
  if (state.route === "chat") {
    return `<button class="button primary" data-action="new-session">新建对话</button>`;
  }
  if (state.route === "skills") {
    return `<button class="button primary" data-action="new-skill">新建技能</button>`;
  }
  if (state.route === "transcripts") {
    return `<button class="button" data-action="refresh-transcripts">刷新</button>`;
  }
  if (state.route === "media") {
    return `<button class="button" data-action="refresh-jobs">刷新任务</button>`;
  }
  return `<button class="button primary" data-action="save-settings">保存设置</button>`;
}

function shell() {
  const connected = Boolean(state.doctor);
  const nav = routes
    .map(
      (item) => `
        <button class="nav-button ${state.route === item.id ? "active" : ""}" data-route="${item.id}">
          <span class="nav-icon">${escapeHtml(item.icon)}</span>
          <span>${escapeHtml(item.label)}</span>
        </button>
      `,
    )
    .join("");

  return `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark"><img src="./assets/sjtulogosilver.png" alt="" /></div>
          <div>
            <div class="brand-title">SJTUFlow</div>
            <div class="brand-subtitle">本地学习工作台</div>
          </div>
        </div>
        <nav class="nav">${nav}</nav>
        <div class="sidebar-footer">
          <span class="status-pill"><span class="status-dot ${connected ? "ok" : ""}"></span>${connected ? "后端已连接" : "等待后端"}</span>
          <div>API: ${escapeHtml(state.apiBase || "same origin")}</div>
        </div>
      </aside>
      <main class="main">
        ${topbar()}
        <section class="content">${content()}</section>
      </main>
      ${notices()}
    </div>
  `;
}

function content() {
  if (state.route === "dashboard") return dashboardView();
  if (state.route === "chat") return chatView();
  if (state.route === "skills") return skillsView();
  if (state.route === "transcripts") return transcriptsView();
  if (state.route === "media") return mediaView();
  return settingsView();
}

function dashboardView() {
  const doctor = state.doctor || {};
  const briefing = state.briefing || {};
  return `
    <div class="grid">
      <section class="panel hero-panel">
        <div class="panel-body hero-copy">
          <div class="hero-kicker">LOCAL STUDY ASSISTANT</div>
          <h2>从课程动态到课堂转写，集中管理你的学习上下文</h2>
          <p>先查看近期作业和公告，再进入学习对话；需要时按需读取 Skills 与 Transcripts，所有资料都保留在本地。</p>
        </div>
        <img class="hero-logo" src="./assets/sjtulogored.png" alt="" />
      </section>
      ${firstRunBanner()}
      <div class="grid three">
        ${metric("学习流程", doctor.skills_loaded ?? "-", "已加载 Skills")}
        ${metric("后端工具", doctor.tools_registered ?? "-", "可调用 API 工具")}
        ${metric("历史对话", state.sessions.length, "本地保存会话")}
      </div>
      <div class="grid two">
        ${briefingPanel("近期作业", briefing.upcoming, "来自 Canvas 的作业与截止时间")}
        ${briefingPanel("课程更新", briefing.updates, "最近公告和课程动态")}
      </div>
      <div class="grid two">
        ${briefingPanel("紧急事项", briefing.urgent, "需要优先处理的学习提醒")}
        ${briefingPanel("系统提醒", briefing.warnings, "配置、Canvas 或连接状态提示")}
      </div>
    </div>
  `;
}

function firstRunBanner() {
  const doctor = state.doctor;
  if (!doctor) {
    return `
    <section class="panel">
        <div class="panel-body">Connect to the local backend to load your setup status.</div>
      </section>
    `;
  }
  const needsModel = doctor.model && doctor.model.provider !== "mock" && !doctor.model.key_configured;
  const needsCanvas = doctor.canvas && !doctor.canvas.token_configured;
  if (!needsModel && !needsCanvas) return "";
  return `
    <section class="panel">
      <div class="panel-head">
        <h2>需要完成配置</h2>
        <button class="button" data-route="settings">检查设置</button>
      </div>
      <div class="panel-body">
        <div class="badge-row">
          ${needsModel ? '<span class="badge warn">模型 key 未配置</span>' : ""}
          ${needsCanvas ? '<span class="badge warn">Canvas token 未配置</span>' : ""}
          ${doctor.model?.provider === "mock" ? '<span class="badge good">Mock 模型可演示</span>' : ""}
        </div>
      </div>
    </section>
  `;
}

function metric(label, value, note) {
  return `
    <section class="panel">
      <div class="panel-body metric">
        <div class="metric-value">${escapeHtml(value)}</div>
        <div>
          <div class="list-title">${escapeHtml(label)}</div>
          <div class="metric-label">${escapeHtml(note)}</div>
        </div>
      </div>
    </section>
  `;
}

function briefingPanel(title, items, subtitle) {
  const body =
    Array.isArray(items) && items.length
      ? `<div class="list">${items.map((item) => briefingItem(item)).join("")}</div>`
      : `<div class="empty">暂无${escapeHtml(title)}。</div>`;
  return `
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>${escapeHtml(title)}</h2>
          <div class="list-meta">${escapeHtml(subtitle)}</div>
        </div>
        ${isLoading("briefing") ? '<span class="badge">Loading</span>' : ""}
      </div>
      <div class="panel-body">${body}</div>
    </section>
  `;
}

function briefingItem(item) {
  if (typeof item !== "object" || item === null) {
    return `<div class="list-item"><div>${escapeHtml(item)}</div></div>`;
  }
  const title = item.title || item.name || item.assignment_id || item.id || item.source || "Canvas item";
  const meta = item.course || item.course_id || item.due_at || item.posted_at || item.message || "";
  return `
    <div class="list-item">
      <div class="list-title">${escapeHtml(title)}</div>
      <div class="list-meta">${escapeHtml(truncate(meta, 180))}</div>
    </div>
  `;
}

function chatView() {
  return `
    <div class="chat-layout">
      <section class="panel history-panel">
        <div class="panel-head">
          <h2>历史对话</h2>
          ${isLoading("sessions") ? '<span class="badge">Loading</span>' : ""}
        </div>
        <div class="panel-body">${sessionList()}</div>
      </section>
      <section class="panel chat-panel">
        <div class="panel-head">
          <div>
            <h2>${escapeHtml(state.activeSession?.title || "当前对话")}</h2>
            <div class="list-meta">${state.activeSession ? `会话 ${escapeHtml(state.activeSession.id.slice(0, 10))}` : "直接输入问题，系统会自动创建对话"}</div>
          </div>
          ${
            state.activeSession
              ? '<button class="button danger" data-action="delete-session">删除</button>'
              : ""
          }
        </div>
        <div class="messages">${messages()}</div>
        ${promptSuggestions()}
        ${composer()}
      </section>
    </div>
  `;
}

function sessionList() {
  if (!state.sessions.length) {
    return `<div class="empty">还没有历史对话。可以直接在右侧输入问题。</div>`;
  }
  return `
    <div class="list">
      ${state.sessions
        .map(
          (session) => `
            <button class="list-item ${state.activeSession?.id === session.id ? "active" : ""}" data-action="open-session" data-id="${escapeHtml(session.id)}">
              <div class="list-title">${escapeHtml(session.title || "Untitled session")}</div>
              <div class="list-meta">${escapeHtml(formatDate(session.updated_at))} · ${escapeHtml(session.message_count || 0)} messages</div>
            </button>
          `,
        )
        .join("")}
    </div>
  `;
}

function messages() {
  const messages = state.activeSession?.messages || [];
  const visible = messages.filter((message) => message.role !== "system");
  if (!visible.length) {
    return `<div class="empty">
      <div>
        <div style="font-weight: 760; color: var(--ink); margin-bottom: 8px;">可以从一个学习问题开始</div>
        <div>例如查看最近作业、整理课程公告，或询问某段课堂转写内容。</div>
        <div style="height: 14px"></div>
        <button class="button primary" data-action="new-session">新建对话</button>
      </div>
    </div>`;
  }
  return visible
    .map((message) => {
      const role = message.role || "assistant";
      const content = typeof message.content === "string" ? message.content : JSON.stringify(message.content, null, 2);
      const label = role === "user" ? "你" : role === "assistant" ? "SJTUFlow" : "工具状态";
      return `<div class="message ${escapeHtml(role)}"><div class="message-role">${escapeHtml(label)}</div>${escapeHtml(content)}</div>`;
    })
    .join("");
}

function promptSuggestions() {
  const prompts = [
    "这周文本分析与大模型课程有什么作业？",
    "帮我按课程整理最近公告和截止时间。",
    "根据已有转写稿总结这节课的重点。",
  ];
  return `
    <div class="prompt-grid">
      ${prompts
        .map((prompt) => `<button class="prompt-button" data-action="use-prompt" data-prompt="${escapeHtml(prompt)}">${escapeHtml(prompt)}</button>`)
        .join("")}
    </div>
  `;
}

function composer() {
  return `
    <form class="composer" data-form="chat">
      <textarea name="message" placeholder="输入你想询问的学习问题。没有会话时会自动新建。" ${isLoading("message") ? "disabled" : ""}></textarea>
      <button class="button primary" title="发送" aria-label="发送" ${!isLoading("message") ? "" : "disabled"}>${isLoading("message") ? "…" : "↑"}</button>
    </form>
  `;
}

function skillsView() {
  return `
    <div class="split-view">
      <section class="panel media-card">
        <div class="panel-head">
          <h2>技能列表</h2>
          ${isLoading("skills") ? '<span class="badge">加载中</span>' : ""}
        </div>
        <div class="panel-body">${skillList()}</div>
      </section>
      <section class="panel media-card">
        <div class="panel-head">
          <div>
            <h2>${escapeHtml(state.skillDraft?.title || state.selectedSkill?.title || "技能详情")}</h2>
            <div class="list-meta">${escapeHtml(state.skillDraft ? "正在编辑我的技能" : state.selectedSkill?.source === "builtin" ? "内置技能，只能查看或复制" : state.selectedSkill?.source === "user" ? "我的技能，可以编辑和删除" : "选择一个技能查看详情")}</div>
          </div>
          ${skillActions()}
        </div>
        <div class="panel-body">${skillDetail()}</div>
      </section>
    </div>
  `;
}

function skillList() {
  if (!state.skills.length) return `<div class="empty">还没有加载到 skill。</div>`;
  return `
    <div class="list">
      ${state.skills
        .map(
          (skill) => `
            <button class="list-item ${state.selectedSkill?.name === skill.name ? "active" : ""}" data-action="open-skill" data-name="${escapeHtml(skill.name)}">
              <div class="badge-row"><span class="badge ${skill.source === "builtin" ? "" : "good"}">${escapeHtml(skillSourceLabel(skill.source))}</span></div>
              <div class="list-title">${escapeHtml(skill.title || skill.name)}</div>
              <div class="list-meta">${escapeHtml(skill.description || "暂无简介")}</div>
            </button>
          `,
        )
        .join("")}
    </div>
  `;
}

function skillActions() {
  if (state.skillDraft) {
    return `
      <button class="button" data-action="cancel-skill">取消</button>
      <button class="button primary" data-action="save-skill">保存</button>
    `;
  }
  if (!state.selectedSkill) return "";
  if (state.selectedSkill.source === "builtin") {
    return `<button class="button" data-action="copy-skill">复制为我的技能</button>`;
  }
  return `
    <button class="button" data-action="edit-skill">编辑</button>
    <button class="button danger" data-action="delete-skill">删除</button>
  `;
}

function skillDetail() {
  const draft = state.skillDraft;
  if (draft) {
    return `
      <div class="form-grid">
        <div class="field span-2">
          <label for="skill-name">名称</label>
          <input id="skill-name" data-field="skill-name" value="${escapeHtml(draft.name)}" />
        </div>
        <div class="field span-2">
          <label for="skill-content">内容</label>
          <textarea id="skill-content" data-field="skill-content">${escapeHtml(draft.content)}</textarea>
        </div>
      </div>
    `;
  }
  if (!state.selectedSkill) return `<div class="empty">选择左侧 skill 后才会读取完整内容。</div>`;
  const guide = skillGuide(state.selectedSkill);
  return `
    <div class="skill-meta">
      <div>
        <span>名称</span>
        <strong>${escapeHtml(state.selectedSkill.name)}</strong>
      </div>
      <div>
        <span>来源</span>
        <strong>${escapeHtml(state.selectedSkill.source === "builtin" ? "内置" : "用户创建")}</strong>
      </div>
    </div>
    <div class="skill-user-guide">
      <h3>这个技能能帮你做什么</h3>
      <p>${escapeHtml(guide.purpose)}</p>
      <div class="guide-grid">
        <div>
          <span>适合什么时候用</span>
          <p>${escapeHtml(guide.timing)}</p>
        </div>
        <div>
          <span>通常会得到什么</span>
          <p>${escapeHtml(guide.output)}</p>
        </div>
      </div>
    </div>
    <details class="raw-skill">
      <summary>查看开发者原文</summary>
      <div class="pre">${escapeHtml(state.selectedSkill.content || "")}</div>
    </details>
  `;
}

function transcriptsView() {
  return `
    <div class="split-view">
      <section class="panel">
        <div class="panel-head">
          <h2>转写列表</h2>
          ${isLoading("transcripts") ? '<span class="badge">加载中</span>' : ""}
        </div>
        <div class="panel-body">
          ${transcriptSearch()}
          ${transcriptList()}
        </div>
      </section>
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>${escapeHtml(state.selectedTranscript?.title || "转写详情")}</h2>
            <div class="list-meta">${escapeHtml(state.selectedTranscript?.source || "选择一条转写后读取全文")}</div>
          </div>
          ${transcriptActions()}
        </div>
        <div class="panel-body">${transcriptDetail()}</div>
      </section>
    </div>
  `;
}

function transcriptSearch() {
  return `
    <form class="toolbar-form" data-form="transcript-search">
      <input name="query" value="${escapeHtml(state.transcriptQuery)}" placeholder="搜索标题、摘要或正文片段" />
      <button class="button primary">搜索</button>
      ${state.transcriptQuery ? '<button class="button" type="button" data-action="clear-transcript-search">清除</button>' : ""}
    </form>
  `;
}

function transcriptActions() {
  if (!state.selectedTranscript) return "";
  return `
    <div class="detail-actions">
      <button class="button" data-action="refresh-transcript-summary">刷新摘要</button>
      <button class="button" data-action="rename-transcript">重命名</button>
      <button class="button danger" data-action="delete-transcript">删除</button>
    </div>
  `;
}

function transcriptList() {
  if (!state.transcripts.length) {
    return `<div class="empty">${state.transcriptQuery ? "没有找到匹配的转写。" : "还没有保存的转写。完成媒体转写后会出现在这里。"}</div>`;
  }
  return `
    <div class="list">
      ${state.transcripts
        .map(
          (item) => `
            <button class="list-item ${state.selectedTranscript?.id === item.id ? "active" : ""}" data-action="open-transcript" data-id="${escapeHtml(item.id)}">
              <div class="list-title">${escapeHtml(item.title || item.id)}</div>
              <div class="list-meta">${escapeHtml(item.snippet ? `匹配片段：${item.snippet}` : item.description || item.source || "暂无简介")}</div>
              <div class="badge-row">
                ${item.matched_fields?.length ? `<span class="badge good">${escapeHtml(item.matched_fields.includes("content") ? "正文匹配" : "信息匹配")}</span>` : ""}
                ${item.updated_at ? `<span class="badge">${escapeHtml(formatDate(item.updated_at))}</span>` : ""}
                ${item.duration_seconds ? `<span class="badge">${escapeHtml(Math.round(item.duration_seconds / 60))} min</span>` : ""}
              </div>
            </button>
          `,
        )
        .join("")}
    </div>
  `;
}

function transcriptDetail() {
  if (!state.selectedTranscript) return `<div class="empty">选择左侧条目后，前端才会按需读取全文。</div>`;
  const content =
    state.selectedTranscript.content ||
    state.selectedTranscript.text ||
    JSON.stringify(state.selectedTranscript, null, 2);
  return `
    <div class="config-summary">
      <div class="summary-row"><span class="summary-key">编号</span><span class="summary-value">${escapeHtml(state.selectedTranscript.id)}</span></div>
      <div class="summary-row"><span class="summary-key">路径</span><span class="summary-value">${escapeHtml(displayPath(state.selectedTranscript.path || ""))}</span></div>
      ${state.selectedTranscript.description ? `<div class="summary-row"><span class="summary-key">摘要</span><span class="summary-value">${escapeHtml(state.selectedTranscript.description)}</span></div>` : ""}
    </div>
    <div style="height: 14px"></div>
    <div class="pre">${escapeHtml(content)}</div>
  `;
}

function mediaView() {
  return `
    <div class="grid two">
      <section class="panel">
        <div class="panel-head">
          <h2>本地媒体</h2>
          <span class="badge">转写任务</span>
        </div>
        <div class="panel-body">
          <form class="grid" data-form="media-local">
            <div class="field">
              <label for="media-path">本地文件路径</label>
              <input id="media-path" name="path" placeholder="~/Desktop/lecture.mp4" />
            </div>
            <div class="field">
              <label for="media-title">转写标题</label>
              <input id="media-title" name="title" placeholder="第 3 次课" />
            </div>
            <div class="field">
              <label for="media-description">说明</label>
              <textarea id="media-description" name="description" placeholder="课程、周次或主题备注"></textarea>
            </div>
            <button class="button primary" ${isLoading("media") ? "disabled" : ""}>开始转写并保存</button>
          </form>
        </div>
      </section>
      <section class="panel">
        <div class="panel-head">
          <h2>已授权 Canvas 媒体流</h2>
          <span class="badge warn">需要登录态</span>
        </div>
        <div class="panel-body">
          <form class="grid" data-form="media-stream">
            <div class="field">
              <label for="stream-url">媒体流 URL</label>
              <input id="stream-url" name="stream_url" placeholder="https://..." />
            </div>
            <div class="field">
              <label for="stream-title">转写标题</label>
              <input id="stream-title" name="title" placeholder="Canvas 课程录像" />
            </div>
            <button class="button primary" ${isLoading("stream") ? "disabled" : ""}>开始媒体流转写</button>
          </form>
        </div>
      </section>
      <section class="panel span-2">
        <div class="panel-head">
          <h2>任务状态</h2>
          ${isLoading("jobs") ? '<span class="badge">加载中</span>' : ""}
        </div>
        <div class="panel-body">${jobList()}</div>
      </section>
    </div>
  `;
}

function jobList() {
  if (!state.jobs.length) return `<div class="empty">还没有媒体转写任务。</div>`;
  return `
    <div class="list">
      ${state.jobs
        .map(
          (job) => `
            <div class="list-item">
              <div class="list-title">${escapeHtml(job.kind || job.id)}</div>
              <div class="list-meta">${escapeHtml(job.status || "")} · ${escapeHtml(job.message || "")}</div>
              <div class="badge-row">
                <span class="badge">${escapeHtml(job.id)}</span>
                <span class="badge">${escapeHtml(Math.round((job.progress || 0) * 100))}%</span>
              </div>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function settingsView() {
  const values = state.config?.values || {};
  const model = values.model || {};
  const canvas = values.canvas || {};
  const workspace = values.workspace || {};
  const agent = values.agent || {};
  const permissions = values.permissions || {};
  return `
    <div class="grid two">
      <section class="panel">
        <div class="panel-head">
          <h2>配置项</h2>
          ${isLoading("config") ? '<span class="badge">加载中</span>' : ""}
        </div>
        <div class="panel-body">
          <form class="form-grid" data-form="settings">
            ${field("model.provider", "模型提供方", model.provider, "mock or openai-compatible")}
            ${field("model.model", "模型名称", model.model)}
            ${field("model.endpoint", "接口地址", model.endpoint)}
            ${field("model.api_key", "API key", "", "留空表示保留已有密钥", "password")}
            ${field("canvas.base_url", "Canvas 地址", canvas.base_url)}
            ${field("canvas.access_token", "Canvas token", "", "留空表示保留已有 token", "password")}
            ${field("workspace.state_dir", "状态目录", workspace.state_dir)}
            ${field("workspace.data_dir", "资料目录", workspace.data_dir)}
            ${field("agent.briefing_window_days", "Briefing 天数", agent.briefing_window_days, "", "number")}
            ${field("agent.max_tool_calls", "最大工具调用", agent.max_tool_calls, "", "number")}
            ${checkbox("permissions.confirm_local_write", "本地写入前确认", permissions.confirm_local_write)}
            ${checkbox("permissions.confirm_destructive", "删除类操作前确认", permissions.confirm_destructive)}
          </form>
        </div>
      </section>
      <section class="panel">
        <div class="panel-head">
          <h2>运行检查</h2>
          ${isLoading("doctor") ? '<span class="badge">加载中</span>' : ""}
        </div>
        <div class="panel-body">${doctorSummary()}</div>
      </section>
    </div>
  `;
}

function field(name, label, value, placeholder = "", type = "text") {
  return `
    <div class="field ${name.includes("endpoint") || name.includes("base_url") ? "span-2" : ""}">
      <label for="${escapeHtml(name)}">${escapeHtml(label)}</label>
      <input id="${escapeHtml(name)}" name="${escapeHtml(name)}" type="${escapeHtml(type)}" value="${escapeHtml(value ?? "")}" placeholder="${escapeHtml(placeholder)}" />
    </div>
  `;
}

function checkbox(name, label, checked) {
  return `
    <label class="field">
      <span>${escapeHtml(label)}</span>
      <select name="${escapeHtml(name)}">
        <option value="true" ${checked ? "selected" : ""}>开启</option>
        <option value="false" ${!checked ? "selected" : ""}>关闭</option>
      </select>
    </label>
  `;
}

function doctorSummary() {
  const doctor = state.doctor;
  if (!doctor) return `<div class="empty">还没有读取到运行检查结果。</div>`;
  const rows = [
    ["配置文件", displayPath(doctor.config)],
    ["状态目录", displayPath(doctor.state_dir)],
    ["资料目录", displayPath(doctor.data_dir)],
    ["模型提供方", doctor.model?.provider],
    ["模型 key", doctor.model?.key_configured ? "已配置" : "未配置"],
    ["Canvas token", doctor.canvas?.token_configured ? "已配置" : "未配置"],
    ["已加载 Skills", doctor.skills_loaded],
    ["后端工具数", doctor.tools_registered],
  ];
  return `
    <div class="config-summary">
      ${rows
        .map(
          ([key, value]) => `
            <div class="summary-row">
              <span class="summary-key">${escapeHtml(key)}</span>
              <span class="summary-value">${escapeHtml(compact(value))}</span>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function notices() {
  if (!state.notices.length) return "";
  return `
    <div class="notice-stack">
      ${state.notices
        .map((notice) => `<div class="notice ${escapeHtml(notice.type)}">${escapeHtml(notice.message)}</div>`)
        .join("")}
    </div>
  `;
}

function render() {
  app.innerHTML = shell();
}

async function createSession() {
  await loadResource("sessions", async () => {
    state.activeSession = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ run_briefing: false }),
    });
    await loadSessions(false);
    notify("New talk created.");
  });
}

async function openSession(id) {
  await loadResource("sessions", async () => {
    state.activeSession = await api(`/api/sessions/${encodeURIComponent(id)}`);
  });
}

async function deleteSession() {
  if (!state.activeSession) return;
  if (!confirm("Delete this local session?")) return;
  const id = state.activeSession.id;
  await loadResource("sessions", async () => {
    await api(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
    state.activeSession = null;
    await loadSessions(false);
    notify("Session deleted.");
  });
}

async function sendMessage(form) {
  const message = new FormData(form).get("message")?.toString().trim();
  if (!message) return;
  setLoading("message", true);
  try {
    if (!state.activeSession) {
      state.activeSession = await api("/api/sessions", {
        method: "POST",
        body: JSON.stringify({ run_briefing: false }),
      });
    }
    state.activeSession = await api(`/api/sessions/${encodeURIComponent(state.activeSession.id)}/messages`, {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    await loadSessions(false);
    form.reset();
  } catch (error) {
    notify(error.message || String(error), "error");
  } finally {
    setLoading("message", false);
  }
}

async function openSkill(name) {
  await loadResource("skill", async () => {
    state.skillDraft = null;
    state.selectedSkill = await api(`/api/skills/${encodeURIComponent(name)}`);
  });
}

function newSkill() {
  state.selectedSkill = null;
  state.skillDraft = {
    name: "my-study-skill",
    content:
      "# My Study Skill\n\n## Purpose\nDescribe what this workflow helps with.\n\n## Required Tools\n- skills.list\n\n## Workflow\n1. Read the task.\n2. Gather only the needed context.\n3. Produce a concise study output.\n\n## Output\nA checklist or short report.\n\n## Safety\nAsk before writing or deleting local files.\n",
  };
  render();
}

function editSkill(copy = false) {
  if (!state.selectedSkill) return;
  const sourceName = state.selectedSkill.name || "skill";
  state.skillDraft = {
    name: copy ? `${sourceName}-copy` : sourceName,
    content: state.selectedSkill.content || "",
  };
  render();
}

async function saveSkill() {
  const name = document.querySelector("[data-field='skill-name']")?.value.trim();
  const content = document.querySelector("[data-field='skill-content']")?.value;
  if (!name || !content) {
    notify("Skill name and content are required.", "error");
    return;
  }
  await loadResource("skill", async () => {
    state.selectedSkill = await api("/api/skills", {
      method: "POST",
      body: JSON.stringify({ name, content, overwrite: true }),
    });
    state.skillDraft = null;
    await loadSkills(false);
    notify("Skill saved.");
  });
}

async function deleteSkill() {
  if (!state.selectedSkill) return;
  if (state.selectedSkill.source === "builtin") {
    notify("Built-in skills can only be copied, not deleted.", "error");
    return;
  }
  if (!confirm(`Delete user skill ${state.selectedSkill.name}?`)) return;
  const name = state.selectedSkill.name;
  await loadResource("skill", async () => {
    await api(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
    state.selectedSkill = null;
    await loadSkills(false);
    notify("Skill deleted.");
  });
}

async function openTranscript(id) {
  await loadResource("transcript", async () => {
    state.selectedTranscript = await api(`/api/transcripts/${encodeURIComponent(id)}`);
  });
}

async function searchTranscripts(form) {
  state.transcriptQuery = new FormData(form).get("query")?.toString().trim() || "";
  await loadTranscripts();
}

async function clearTranscriptSearch() {
  state.transcriptQuery = "";
  await loadTranscripts();
}

async function renameTranscript() {
  if (!state.selectedTranscript) return;
  const currentTitle = state.selectedTranscript.title || "";
  const title = prompt("输入新的转写标题", currentTitle)?.trim();
  if (!title || title === currentTitle) return;
  const id = state.selectedTranscript.id;
  await loadResource("transcript", async () => {
    const renamed = await api(`/api/transcripts/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ title, overwrite: false }),
    });
    await loadTranscripts(false);
    state.selectedTranscript = await api(`/api/transcripts/${encodeURIComponent(renamed.id || id)}`);
    notify("转写标题已更新。");
  });
}

async function deleteTranscript() {
  if (!state.selectedTranscript) return;
  const title = state.selectedTranscript.title || state.selectedTranscript.id;
  if (!confirm(`删除转写「${title}」？这个操作会删除本地保存的转写文件。`)) return;
  const id = state.selectedTranscript.id;
  await loadResource("transcript", async () => {
    await api(`/api/transcripts/${encodeURIComponent(id)}`, { method: "DELETE" });
    state.selectedTranscript = null;
    await loadTranscripts(false);
    notify("转写已删除。");
  });
}

async function refreshTranscriptSummary() {
  if (!state.selectedTranscript) return;
  const id = state.selectedTranscript.id;
  await loadResource("transcript", async () => {
    const refreshed = await api(`/api/transcripts/${encodeURIComponent(id)}/summary`, {
      method: "POST",
      body: JSON.stringify({ summary: null }),
    });
    await loadTranscripts(false);
    state.selectedTranscript = await api(`/api/transcripts/${encodeURIComponent(refreshed.id || id)}`);
    notify("摘要已刷新。");
  });
}

async function saveSettings() {
  const form = document.querySelector("[data-form='settings']");
  if (!form) return;
  const data = new FormData(form);
  const updates = {};
  for (const [key, value] of data.entries()) {
    if ((key === "model.api_key" || key === "canvas.access_token") && !value) continue;
    if (value === "") continue;
    if (value === "true" || value === "false") updates[key] = value === "true";
    else if (["agent.briefing_window_days", "agent.max_tool_calls"].includes(key)) updates[key] = Number(value);
    else updates[key] = value;
  }
  await loadResource("config", async () => {
    state.config = await api("/api/config", {
      method: "PUT",
      body: JSON.stringify({ updates }),
    });
    await loadDoctor(false);
    notify("Settings saved.");
  });
}

async function startLocalMedia(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  if (!data.path) {
    notify("Local file path is required.", "error");
    return;
  }
  await loadResource("media", async () => {
    await api("/api/media/transcribe-and-save", {
      method: "POST",
      body: JSON.stringify({
        path: data.path,
        title: data.title || null,
        description: data.description || "",
        sync: false,
      }),
    });
    await loadJobs(false);
    notify("Media job started.");
  });
}

async function startStreamMedia(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  if (!data.stream_url || !data.title) {
    notify("Stream URL and title are required.", "error");
    return;
  }
  await loadResource("stream", async () => {
    await api("/api/media/transcribe-stream", {
      method: "POST",
      body: JSON.stringify({
        stream_url: data.stream_url,
        title: data.title,
        sync: false,
      }),
    });
    await loadJobs(false);
    notify("Stream transcript job started.");
  });
}

document.addEventListener("click", (event) => {
  const routeTarget = event.target.closest("[data-route]");
  if (routeTarget) {
    setRoute(routeTarget.dataset.route);
    return;
  }

  const actionTarget = event.target.closest("[data-action]");
  if (!actionTarget) return;
  const action = actionTarget.dataset.action;

  if (action === "refresh-core") refreshCore();
  if (action === "new-session") createSession();
  if (action === "open-session") openSession(actionTarget.dataset.id);
  if (action === "delete-session") deleteSession();
  if (action === "new-skill") newSkill();
  if (action === "open-skill") openSkill(actionTarget.dataset.name);
  if (action === "edit-skill") editSkill(false);
  if (action === "copy-skill") editSkill(true);
  if (action === "cancel-skill") {
    state.skillDraft = null;
    render();
  }
  if (action === "save-skill") saveSkill();
  if (action === "delete-skill") deleteSkill();
  if (action === "open-transcript") openTranscript(actionTarget.dataset.id);
  if (action === "clear-transcript-search") clearTranscriptSearch();
  if (action === "rename-transcript") renameTranscript();
  if (action === "delete-transcript") deleteTranscript();
  if (action === "refresh-transcript-summary") refreshTranscriptSummary();
  if (action === "refresh-transcripts") loadTranscripts();
  if (action === "refresh-jobs") loadJobs();
  if (action === "save-settings") saveSettings();
  if (action === "use-prompt") {
    const input = document.querySelector(".composer textarea");
    if (input) {
      input.value = actionTarget.dataset.prompt || "";
      input.focus();
    }
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest("form");
  if (!form) return;
  event.preventDefault();
  const formName = form.dataset.form;
  if (formName === "chat") sendMessage(form);
  if (formName === "transcript-search") searchTranscripts(form);
  if (formName === "media-local") startLocalMedia(form);
  if (formName === "media-stream") startStreamMedia(form);
});

render();
refreshCore();
