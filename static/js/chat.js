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

    // 体验券状态
    const couponCard = reactive({
      visible: false,
      msgIdx: -1,
      claimed: false,
      showForm: false,
      countdown: 60,
      timerId: null,
    });
    const leadForm = reactive({
      name: '',
      phone: '',
      submitting: false,
    });

    const { $api, $toast, $icons, $chatStream } = Vue.getCurrentInstance().appContext.config.globalProperties;
    const nextTick = Vue.nextTick;

    // 反馈状态：{ msgIdx: 'up' | 'down' }
    const feedbackState = reactive({});

    // 保存 user_id
    function setUserId(val) {
      userId.value = val || 'anonymous';
      localStorage.setItem('echomind_user_id', userId.value);
    }

    // ── 显示体验券卡片（由后端 show_coupon 字段控制）──────
    function showCouponCard(msgIdx) {
      couponCard.visible = true;
      couponCard.msgIdx = msgIdx;
      // 清理旧定时器
      if (couponCard.timerId) {
        clearInterval(couponCard.timerId);
        couponCard.timerId = null;
      }
      couponCard.claimed = false;
      couponCard.showForm = false;
      couponCard.countdown = 60;
      leadForm.name = '';
      leadForm.phone = '';
    }

    // ── 领取体验券 ───────────────────────────────────
    async function claimCoupon() {
      try {
        const result = await $api.claimCoupon(userId.value, convId.value);
        if (result.status === 'ok') {
          couponCard.claimed = true;
          couponCard.showForm = true;
          startCountdown();
          $toast('success', '🎉 已领取体验券！请在 60s 内填写留资信息');
        } else if (result.status === 'duplicate') {
          couponCard.claimed = true;
          couponCard.showForm = true;
          startCountdown();
          $toast('info', '您已领取过体验券，请填写留资信息');
        } else if (result.status === 'sold_out') {
          $toast('error', '体验券已发放完毕');
          couponCard.visible = false;
        } else if (result.status === 'cooldown') {
          $toast('info', '24h 冷却中，暂无法领取');
          couponCard.visible = false;
        } else {
          $toast('error', result.message || '领取失败');
        }
      } catch (err) {
        $toast('error', `领取失败: ${err.message}`);
      }
    }

    // ── 关闭体验券 ───────────────────────────────────
    function dismissCoupon() {
      if (couponCard.timerId) {
        clearInterval(couponCard.timerId);
        couponCard.timerId = null;
      }
      couponCard.visible = false;
      couponCard.showForm = false;
      couponCard.claimed = false;
      $toast('info', '已关闭体验券');
    }

    // ── 倒计时 ───────────────────────────────────────
    function startCountdown() {
      if (couponCard.timerId) clearInterval(couponCard.timerId);
      couponCard.countdown = 60;
      couponCard.timerId = setInterval(() => {
        couponCard.countdown--;
        if (couponCard.countdown <= 0) {
          clearInterval(couponCard.timerId);
          couponCard.timerId = null;
          couponCard.showForm = false;
          couponCard.visible = false;
          couponCard.claimed = false;
          $toast('warning', '⏰ 表单填写超时，体验券已释放');
        }
      }, 1000);
    }

    // ── 提交留资表单 ─────────────────────────────────
    async function submitLeadForm() {
      const name = leadForm.name.trim();
      const phone = leadForm.phone.trim();
      if (!name) { $toast('error', '请输入姓名'); return; }
      if (!phone) { $toast('error', '请输入手机号'); return; }
      if (!/^1\d{10}$/.test(phone)) { $toast('error', '请输入正确的 11 位手机号'); return; }

      leadForm.submitting = true;
      try {
        const result = await $api.submitCouponLead(userId.value, name, phone, convId.value);
        if (result.status === 'ok') {
          $toast('success', '✅ 试驾体验券已锁定！工作人员将尽快联系您');
          if (couponCard.timerId) {
            clearInterval(couponCard.timerId);
            couponCard.timerId = null;
          }
          couponCard.showForm = false;
          couponCard.visible = false;
        } else {
          $toast('error', result.message || '提交失败');
        }
      } catch (err) {
        $toast('error', `提交失败: ${err.message}`);
      } finally {
        leadForm.submitting = false;
      }
    }
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
          if (d.show_coupon) showCouponCard(msgIdx);
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
          if (data.show_coupon) showCouponCard(msgIdx);
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
      for (const key in feedbackState) delete feedbackState[key];
      $toast('info', '已开始新对话');
    }

    // ── 处理键盘事件 ───────────────────────────────────────
    function handleKeydown(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    }

    // ── Bad Case 反馈（👎）─────────────────────────────────
    async function reportBadCase(msgIdx) {
      const msg = messages[msgIdx];
      if (!msg || msg.role !== 'ai' || feedbackState[msgIdx] === 'down') return;
      feedbackState[msgIdx] = 'down';

      try {
        await $api.feedbackBadcase({
          query: messages[msgIdx - 1]?.content || '',
          response: msg.content || '',
          predicted_sub_tasks: msg.meta?.skills || [],
          conv_id: convId.value || '',
          user_id: userId.value || '',
        });
        $toast('info', '已反馈，感谢帮助改进！');
      } catch (err) {
        feedbackState[msgIdx] = null;
        console.warn('反馈失败:', err);
      }
    }

    // ── 好评（👍）──────────────────────────────────────────
    function goodFeedback(msgIdx) {
      if (feedbackState[msgIdx] === 'up') {
        feedbackState[msgIdx] = null;
      } else {
        feedbackState[msgIdx] = 'up';
      }
    }

    return {
      messages, userInput, loading, error, convId,
      showInfo, currentMsg, streaming, userId,
      sendMessage, newConversation, handleKeydown, autoResize,
      setUserId, $icons,
      feedbackState, reportBadCase, goodFeedback,
      couponCard, leadForm, claimCoupon, dismissCoupon, submitLeadForm, startCountdown,
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
            <!-- 体验券卡片（在 AI 回复末尾展示） -->
            <div v-if="msg.role === 'ai' && couponCard.visible && couponCard.msgIdx === i && !couponCard.showForm" class="coupon-card glass-sm">
              <div class="coupon-card-header">
                <span v-html="$icons.gift" class="coupon-icon"></span>
                <div>
                  <div class="coupon-title">🎁 试驾体验券</div>
                  <div class="coupon-desc">到店试驾即可获得专属礼品，赶快领取吧！</div>
                </div>
              </div>
              <div class="coupon-card-actions">
                <button class="btn btn-primary btn-sm" @click="claimCoupon" :disabled="loading">
                  确认领取
                </button>
                <button class="btn btn-secondary btn-sm" @click="dismissCoupon">
                  暂不需要
                </button>
              </div>
            </div>
            <!-- 留资表单（领取后弹出） -->
            <div v-if="msg.role === 'ai' && couponCard.visible && couponCard.showForm && couponCard.msgIdx === i" class="lead-form glass-sm">
              <div class="lead-form-header">
                <span v-html="$icons.gift" class="coupon-icon"></span>
                <span class="coupon-title">填写信息锁定体验券</span>
                <span class="lead-countdown" :class="{ urgent: couponCard.countdown <= 15 }">
                  {{ couponCard.countdown }}s
                </span>
              </div>
              <div class="lead-form-body">
                <input v-model="leadForm.name" placeholder="您的姓名" class="lead-input" maxlength="20" :disabled="leadForm.submitting" />
                <input v-model="leadForm.phone" placeholder="手机号" class="lead-input" maxlength="11" :disabled="leadForm.submitting" />
                <button class="btn btn-primary btn-sm lead-submit" @click="submitLeadForm" :disabled="leadForm.submitting">
                  <span v-if="leadForm.submitting" v-html="$icons.loader"></span>
                  <span v-else>提交</span>
                </button>
              </div>
            </div>
            <div v-if="msg.meta && !(streaming && i === messages.length - 1)" class="message-meta">
              <span v-if="msg.meta.intent && msg.meta.intent !== 'error'" class="meta-tag intent">{{ msg.meta.intent }}</span>
              <span v-if="msg.meta.emotion && msg.meta.emotion !== 'error'" class="meta-tag emotion">{{ msg.meta.emotion }}</span>
              <span v-for="s in msg.meta.skills" :key="s" class="meta-tag skill">{{ s }}</span>
              <span v-if="msg.meta.knowledge" class="meta-tag knowledge">知识库</span>
              <span v-if="msg.meta.latency" class="meta-tag latency">{{ msg.meta.latency.toFixed(0) }} ms</span>
            </div>
            <!-- 反馈按钮（仅 AI 消息且非流式时显示） -->
            <div v-if="msg.role === 'ai' && msg.meta && !(streaming && i === messages.length - 1)" class="feedback-bar">
              <button class="feedback-btn up" :class="{ active: feedbackState[i] === 'up' }"
                @click="goodFeedback(i)" :title="feedbackState[i] === 'up' ? '取消' : '有帮助'"
                v-html="$icons.thumbsUp"></button>
              <button class="feedback-btn down" :class="{ active: feedbackState[i] === 'down' }"
                @click="reportBadCase(i)" :title="反馈不准确"
                v-html="$icons.thumbsDown"></button>
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
