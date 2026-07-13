
/* ══════════════════════════════════════════════════════════════
   EchoMind — Vue 3 应用入口 & API 层
   ══════════════════════════════════════════════════════════════ */

const { createApp, ref, reactive, computed, onMounted, nextTick, watch } = Vue;

// ── API Client ───────────────────────────────────────────────
const api = {
  base: '',

  async _fetch(method, path, body) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    };
    if (body !== undefined) opts.body = JSON.stringify(body);

    const res = await fetch(`${this.base}${path}`, opts);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `请求失败 (${res.status})`);
    return data;
  },

  health:       ()                     => api._fetch('GET', '/health'),
  chat:         (msg, uid, cid)        => api._fetch('POST', '/chat', { message: msg, user_id: uid || 'anonymous', conv_id: cid || null }),
  kbStats:      ()                     => api._fetch('GET', '/knowledge/stats'),
  kbList:       ()                     => api._fetch('GET', '/knowledge/list'),
  kbAdd:        (docs)                 => api._fetch('POST', '/knowledge/add', { documents: docs }),
  kbUpload:     async (file)            => { const fd = new FormData(); fd.append('file', file); const r = await fetch(`${api.base}/knowledge/upload`, { method: 'POST', body: fd }); const d = await r.json(); if (!r.ok) throw new Error(d.detail || `上传失败 (${r.status})`); return d; },
  kbDelete:     (id)                   => api._fetch('DELETE', `/knowledge/${encodeURIComponent(id)}`),
  kbClear:      ()                     => api._fetch('DELETE', '/knowledge'),
  memProfiles:  ()                     => api._fetch('GET', '/memory/profiles'),
  memEpisodic:  ()                     => api._fetch('GET', '/memory/episodic'),
  memDeleteProfile:  (uid)             => api._fetch('DELETE', '/memory/profile' + (uid ? `?user_id=${encodeURIComponent(uid)}` : '')),
  memDeleteEpisodic: (uid)             => api._fetch('DELETE', '/memory/episodic' + (uid ? `?user_id=${encodeURIComponent(uid)}` : '')),
  feedbackBadcase:   (data)            => api._fetch('POST', '/feedback/badcase', data),
  claimCoupon:       (uid, cid)        => api._fetch('POST', '/coupon/claim', { user_id: uid, conv_id: cid }),
  submitCouponLead:  (uid, name, phone, cid) => api._fetch('POST', '/coupon/lead', { user_id: uid, name, phone, conv_id: cid }),
  couponStats:       ()                => api._fetch('GET', '/coupon/stats'),
  couponCheck:       (uid)             => api._fetch('GET', `/coupon/check?user_id=${encodeURIComponent(uid)}`),
};

// 流式对话：返回一个 {meta, tokens[], done} 对象
async function chatStream(message, userId, convId, onToken) {
  const res = await fetch('/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, user_id: userId || 'anonymous', conv_id: convId || null }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `请求失败 (${res.status})` }));
    throw new Error(err.detail || `请求失败 (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let meta = null;
  let tokens = [];
  let done = null;

  while (true) {
    const { done: finished, value } = await reader.read();
    if (finished) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';  // 未完成的行留到下次

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || !trimmed.startsWith('data: ')) continue;
      try {
        const data = JSON.parse(trimmed.slice(6));
        if (data.type === 'meta') {
          meta = data;
        } else if (data.type === 'token') {
          tokens.push(data.text);
          if (onToken) onToken(data.text);
        } else if (data.type === 'done') {
          done = data;
        }
      } catch { /* 忽略解析失败的行 */ }
    }
  }

  return { meta, response: tokens.join(''), done };
}

// ── Toast 系统 ───────────────────────────────────────────────
const toasts = reactive([]);

function showToast(type, message) {
  const id = Date.now() + Math.random();
  toasts.push({ id, type, message });
  setTimeout(() => {
    const i = toasts.findIndex(t => t.id === id);
    if (i > -1) toasts.splice(i, 1);
  }, 4000);
}

const toastIcons = {
  success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>',
  error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>',
  warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 22h20L12 2z"/><path d="M12 10v4M12 18h.01"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
};

// ── Icons 集合 ───────────────────────────────────────────────
const icons = {
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>',
  book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg>',
  brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 017 4.5v13a2.5 2.5 0 005 0v-13A2.5 2.5 0 019.5 2z"/><path d="M14.5 2A2.5 2.5 0 0112 4.5v13a2.5 2.5 0 005 0v-13A2.5 2.5 0 0114.5 2z"/><path d="M4.5 6h15"/><path d="M4.5 10h15"/><path d="M4.5 14h15"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>',
  chevronDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
  zap: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>',
  loader: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spinner"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>',
  alertCircle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>',
  user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
  database: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>',
  files: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg>',
  thumbsUp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14zM7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3"/></svg>',
  thumbsDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9a2 2 0 002 2.3H10zM17 2h3a2 2 0 012 2v7a2 2 0 01-2 2h-3"/></svg>',
  gift: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 12v6a2 2 0 01-2 2H6a2 2 0 01-2-2v-6"/><path d="M22 7H2v5h20V7z"/><path d="M12 22V7"/><path d="M12 7H7.5a2.5 2.5 0 010-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 000-5C13 2 12 7 12 7z"/></svg>',
  clockAlert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/><path d="M12 18h.01"/></svg>',
};

// ── 创建 Vue 应用（模板在 index.html 中）────────────────────────
const app = createApp({
  setup() {
    const activeTab = ref('chat');
    return { activeTab, toasts, toastIcons };
  },
  data: () => ({
    tabs: [
      { id: 'chat',      label: 'Chat',     icon: icons.chat },
      { id: 'knowledge', label: 'Knowledge', icon: icons.book },
      { id: 'memory',    label: 'Memory',   icon: icons.brain },
    ],
  }),
});

// ── 注册全局属性 ─────────────────────────────────────────────
app.config.globalProperties.$api = api;
app.config.globalProperties.$toast = showToast;
app.config.globalProperties.$icons = icons;
app.config.globalProperties.$chatStream = chatStream;

// ── 注册组件 ─────────────────────────────────────────────────
app.component('chat-view',      ChatView);
app.component('knowledge-view', KnowledgeView);
app.component('memory-view',    MemoryView);

// ── 挂载 ─────────────────────────────────────────────────────
app.mount('#app');
