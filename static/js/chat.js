/* ══════════════════════════════════════════════════════════════
   EchoMind — ChatView 组件
   流式对话 + 用户/会话管理
   ══════════════════════════════════════════════════════════════ */

const ChatView = {
  name: 'ChatView',

  setup() {
    const messages    = reactive([]);
    const userInput   = ref('');
    const loading     = ref(false);
    const error       = ref(null);
    const convId      = ref(localStorage.getItem('echomind_conv_id'));
    const showInfo    = ref(true);
    const currentMsg  = ref(null);
    const streaming   = ref(false);

    // 用户 & 会话管理
    const userId = ref(localStorage.getItem('echomind_user_id') || ('user_' + Date.now().toString(36)));

    const { $api, $toast, $icons, $chatStream } = Vue.getCurrentInstance().appContext.config.globalProperties;
    const nextTick = Vue.nextTick;

    // 保存 user_id
    function setUserId(val) {
      userId.value = val || 'anonymous';
      localStorage.setItem('echomind_user_id', userId.value);
    }

    // ── 自动调整 textarea 高度 ─────────────────────────────
    function autoResize(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 150) + 'px';
    }

    // ── 发送消息（流式优先） ───────────────────────────────
    async function sendMessage() {
      const text = userInput.value.trim();
      if (!text || loading.value) return;

      // 添加用户消息
      messages.push({ role: 'user', content: text, meta: null });
      userInput.value = '';
      loading.value = true;
      streaming.value = true;
      error.value = null;
      currentMsg.value = null;

      await nextTick();
      const textarea = document.querySelector('.chat-input');
      if (textarea) autoResize(textarea);
      scrollToBottom();

      // 添加一个空的 AI 消息占位
      const msgIdx = messages.length;
      messages.push({ role: 'ai', content: '', meta: null });

      let fullResponse = '';
      let streamMeta = null;

      try {
        // 尝试流式请求
        const result = await $chatStream(
          text,
          userId.value,
          convId.value,
          (token) => {
            fullResponse += token;
            messages[msgIdx].content = fullResponse;
            scrollToBottom();
          }
        );

        convId.value = result.done?.conv_id || convId.value || result.meta?.conv_id;
        if (convId.value) localStorage.setItem('echomind_conv_id', convId.value);
        streamMeta = result.meta;

        if (result.done) {
          // 用 done 里的完整信息更新
          const d = result.done;
          if (d.response && !fullResponse) {
            fullResponse = d.response;
            messages[msgIdx].content = fullResponse;
          }
          const sks = (d.skill_statuses || []).filter(s => s.status === 'success').map(s => s.name);
          const metaObj = {
            intent:     d.primary_intent || streamMeta?.primary_intent || '',
            emotion:    d.emotion || streamMeta?.emotion || '',
            skills:     sks,
            knowledge:  d.knowledge_used || false,
            latency:    d.latency_ms || 0,
            skillAll:   d.skill_statuses || streamMeta?.skill_statuses || [],
          };
          messages[msgIdx].meta = metaObj;
          currentMsg.value = metaObj;
        } else if (streamMeta) {
          // 只有 meta，没有 done（异常情况）
          const sks = (streamMeta.skill_statuses || []).filter(s => s.status === 'success').map(s => s.name);
          const metaObj = {
            intent:     streamMeta.primary_intent || '',
            emotion:    streamMeta.emotion || '',
            skills:     sks,
            knowledge:  streamMeta.need_rag || false,
            latency:    0,
            skillAll:   streamMeta.skill_statuses || [],
          };
          messages[msgIdx].meta = metaObj;
          currentMsg.value = metaObj;
        }

      } catch (streamErr) {
        // 流式失败，降级为非流式
        console.warn('流式请求失败，降级为非流式:', streamErr);
        try {
          const data = await $api.chat(text, userId.value, convId.value);
          convId.value = data.conv_id;
          const sks = (data.skill_statuses || []).filter(s => s.status === 'success').map(s => s.name);
          const metaObj = {
            intent:     data.primary_intent,
            emotion:    data.emotion,
            skills:     sks,
            knowledge:  data.knowledge_used,
            latency:    data.latency_ms,
            skillAll:   data.skill_statuses || [],
          };
          messages[msgIdx].content = data.response;
          messages[msgIdx].meta = metaObj;
          currentMsg.value = metaObj;
        } catch (err) {
          error.value = err.message;
          $toast('error', `请求失败: ${err.message}`);
          messages[msgIdx].content = `抱歉，发生了错误: ${err.message}`;
          messages[msgIdx].meta = { intent: 'error', emotion: 'error', skills: [], knowledge: false, latency: 0, skillAll: [] };
        }
      } finally {
        loading.value = false;
        streaming.value = false;
        scrollToBottom();
      }
    }

    // ── 自动滚动 ───────────────────────────────────────────
    function scrollToBottom() {
      nextTick(() => {
        const container = document.querySelector('.chat-messages');
        if (container) container.scrollTop = container.scrollHeight;
      });
    }

    // ── 新对话 ─────────────────────────────────────────────
    function newConversation() {
      messages.splice(0);
      convId.value = null;
      localStorage.removeItem('echomind_conv_id');
      currentMsg.value = null;
      error.value = null;
      $toast('info', '已开始新对话');
    }

    // ── 处理键盘事件 ───────────────────────────────────────
    function handleKeydown(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    }

    return {
      messages, userInput, loading, error, convId,
      showInfo, currentMsg, streaming, userId,
      sendMessage, newConversation, handleKeydown, autoResize,
      setUserId, $icons,
    };
  },

  template: `
    <div class="chat-view">
      <!-- 用户 & 会话工具栏 -->
      <div style="display:flex;align-items:center;gap:12px;padding:8px 0 4px;flex-shrink:0;flex-wrap:wrap">
        <div class="info-item" style="font-size:12px">
          <span v-html="$icons.user" style="width:14px;height:14px;color:var(--accent);flex-shrink:0"></span>
          <input v-model="userId" @change="setUserId(userId)" placeholder="用户 ID"
            style="background:transparent;border:none;color:var(--text-secondary);font-size:12px;font-family:inherit;outline:none;width:140px;padding:2px 6px;border-radius:4px"
            @focus="$event.target.style.background='rgba(255,255,255,0.06)'"
            @blur="$event.target.style.background='transparent'"
          />
        </div>
        <span style="color:var(--text-muted);font-size:11px" v-if="convId">会话: {{ convId.slice(0,10) }}...</span>
        <button class="btn-sm" style="background:var(--glass-bg);border:1px solid var(--glass-border);color:var(--text-secondary);border-radius:8px"
          @click="newConversation" title="新对话">
          <span v-html="$icons.plus" style="width:14px;height:14px;display:inline-block;vertical-align:middle"></span>
          新对话
        </button>
      </div>

      <!-- 消息列表 -->
      <div class="chat-messages">
        <!-- 空状态 -->
        <div v-if="messages.length === 0" class="chat-empty">
          <div v-html="$icons.moon"></div>
          <h3>EchoMind 智能客服</h3>
          <p>您好！我是 EchoMind AI 助手。请在下方的输入框输入您的问题。</p>
        </div>

        <!-- 消息 -->
        <template v-for="(msg, i) in messages" :key="i">
          <div :class="['chat-message', msg.role]">
            <div class="message-bubble">{{ msg.content }}<span v-if="msg.role === 'ai' && streaming && i === messages.length - 1" class="stream-cursor">▌</span></div>
            <div v-if="msg.meta && !(streaming && i === messages.length - 1)" class="message-meta">
              <span v-if="msg.meta.intent && msg.meta.intent !== 'error'" class="meta-tag intent">{{ msg.meta.intent }}</span>
              <span v-if="msg.meta.emotion && msg.meta.emotion !== 'error'" class="meta-tag emotion">{{ msg.meta.emotion }}</span>
              <span v-for="s in msg.meta.skills" :key="s" class="meta-tag skill">{{ s }}</span>
              <span v-if="msg.meta.knowledge" class="meta-tag knowledge">知识库</span>
              <span v-if="msg.meta.latency" class="meta-tag latency">{{ msg.meta.latency.toFixed(0) }} ms</span>
            </div>
          </div>
        </template>

        <!-- 正在输入（非流式降级时显示） -->
        <div v-if="loading && !streaming" :class="['typing-indicator', { active: loading }]">
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
        </div>
      </div>

      <!-- 信息面板 -->
      <div v-if="currentMsg && messages.length > 1" :class="['chat-info-panel', { visible: showInfo }]">
        <div class="info-item" v-if="convId">
          <span v-html="$icons.info"></span>
          <span class="label">会话:</span>
          <span class="value" style="font-family:monospace;font-size:11px">{{ convId.slice(0, 12) }}...</span>
        </div>
        <div class="info-item">
          <span v-html="$icons.zap"></span>
          <span class="label">意图:</span>
          <span class="value">{{ currentMsg.intent || '-' }}</span>
        </div>
        <div class="info-item">
          <span v-html="$icons.zap" style="color:#4fc3f7"></span>
          <span class="label">情绪:</span>
          <span class="value">{{ currentMsg.emotion || '-' }}</span>
        </div>
        <div v-if="currentMsg.skillAll && currentMsg.skillAll.length" class="info-item">
          <span v-html="$icons.check" style="color:var(--success)"></span>
          <span class="label">技能:</span>
          <span class="value">
            <template v-for="s in currentMsg.skillAll" :key="s.name">
              <span :style="{ color: s.status === 'success' ? 'var(--success)' : s.status === 'failed' ? 'var(--error)' : 'var(--text-secondary)' }"
              >{{ s.name }}{{ s.status === 'success' ? '✓' : s.status === 'failed' ? '✗' : '' }}</span>
              {{ ' ' }}
            </template>
          </span>
        </div>
        <div class="info-item" v-if="currentMsg.latency && currentMsg.latency > 0">
          <span v-html="$icons.clock"></span>
          <span class="label">延迟:</span>
          <span class="value">{{ currentMsg.latency.toFixed(0) }} ms</span>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="chat-input-area">
        <textarea
          v-model="userInput"
          @keydown="handleKeydown"
          @input="autoResize($event.target)"
          placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
          class="chat-input"
          rows="1"
          :disabled="loading"
        ></textarea>
        <button
          class="btn-send"
          @click="sendMessage"
          :disabled="!userInput.trim() || loading"
          v-html="$icons.send"
          title="发送"
        ></button>
      </div>
    </div>
  `,
};
