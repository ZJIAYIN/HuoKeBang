/* ══════════════════════════════════════════════════════════════
   EchoMind — MemoryView 组件
   记忆系统查看：用户画像 + 情景记忆
   ══════════════════════════════════════════════════════════════ */

const MemoryView = {
  name: 'MemoryView',

  setup() {
    const section   = ref('profiles');   // profiles | episodic
    const profiles  = ref([]);
    const episodic  = ref([]);
    const loading   = ref(false);
    const deleting  = ref(false);
    const error     = ref(null);

    const { $api, $toast, $icons } = Vue.getCurrentInstance().appContext.config.globalProperties;

    // ── 加载用户画像 ─────────────────────────────────────
    async function loadProfiles() {
      loading.value = true;
      error.value = null;
      try {
        const data = await $api.memProfiles();
        profiles.value = data.profiles || [];
      } catch (err) {
        error.value = err.message;
        $toast('error', `加载用户画像失败: ${err.message}`);
      } finally {
        loading.value = false;
      }
    }

    // ── 加载情景记忆 ─────────────────────────────────────
    async function loadEpisodic() {
      loading.value = true;
      error.value = null;
      try {
        const data = await $api.memEpisodic();
        episodic.value = data.episodic || [];
      } catch (err) {
        error.value = err.message;
        $toast('error', `加载情景记忆失败: ${err.message}`);
      } finally {
        loading.value = false;
      }
    }

    // ── 切换标签 ─────────────────────────────────────────
    function switchSection(s) {
      section.value = s;
      if (s === 'profiles' && !profiles.value.length) loadProfiles();
      if (s === 'episodic' && !episodic.value.length) loadEpisodic();
    }

    // ── 删除 ────────────────────────────────────────────
    async function deleteProfiles(userId) {
      const label = userId ? `用户 ${userId}` : '全部';
      if (!confirm(`确认清除${label}的用户画像？此操作不可恢复。`)) return;
      deleting.value = true;
      try {
        await $api.memDeleteProfile(userId || null);
        $toast('success', `用户画像已清除 (${label})`);
        profiles.value = [];
        loadProfiles();
      } catch (err) {
        $toast('error', `清除失败: ${err.message}`);
      } finally {
        deleting.value = false;
      }
    }

    async function deleteEpisodic(userId) {
      const label = userId ? `用户 ${userId}` : '全部';
      if (!confirm(`确认清除${label}的情景记忆？此操作不可恢复。`)) return;
      deleting.value = true;
      try {
        await $api.memDeleteEpisodic(userId || null);
        $toast('success', `情景记忆已清除 (${label})`);
        episodic.value = [];
        loadEpisodic();
      } catch (err) {
        $toast('error', `清除失败: ${err.message}`);
      } finally {
        deleting.value = false;
      }
    }

    // ── 格式化时间 ───────────────────────────────────────
    function formatTime(ts) {
      if (!ts) return '-';
      try {
        return new Date(ts).toLocaleString('zh-CN', {
          month: 'short', day: 'numeric',
          hour: '2-digit', minute: '2-digit',
        });
      } catch { return String(ts); }
    }

    // ── 渲染值 ─────────────────────────────────────────────
    function renderValue(val) {
      if (val === null || val === undefined) return '-';
      if (typeof val === 'object') return JSON.stringify(val, null, 1);
      return String(val);
    }

    // ── 初始加载 ─────────────────────────────────────────
    loadProfiles();

    return {
      section, profiles, episodic, loading, deleting, error,
      switchSection, formatTime, renderValue, $icons,
      deleteProfiles, deleteEpisodic,
    };
  },

  template: `
    <div class="memory-view">
      <!-- 标签 -->
      <div class="section-tabs">
        <button :class="['section-tab', { active: section === 'profiles' }]" @click="switchSection('profiles')">
          用户画像
        </button>
        <button :class="['section-tab', { active: section === 'episodic' }]" @click="switchSection('episodic')">
          情景记忆
        </button>
      </div>

      <!-- ── 用户画像 ────────────────────────────────── -->
      <div v-show="section === 'profiles'">
        <!-- 加载 -->
        <div v-if="loading" class="loading-overlay">
          <div class="spinner"></div>
          <span>加载用户画像...</span>
        </div>

        <!-- 错误 -->
        <div v-else-if="error" class="error-state">
          <p>{{ error }}</p>
          <button class="btn btn-secondary mt-8" @click="loadProfiles">重试</button>
        </div>

        <!-- 空 -->
        <div v-else-if="profiles.length === 0" class="memory-empty">
          <div v-html="$icons.user" style="width:48px;height:48px;opacity:0.3;margin:0 auto 12px"></div>
          <p>暂无用户画像数据</p>
        </div>

        <!-- 列表 -->
        <div v-else>
          <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
            <button class="btn btn-sm btn-danger" :disabled="deleting" @click="deleteProfiles('')">
              <span v-if="deleting" class="spinner-sm"></span>
              <span v-else>🗑</span> 清除全部
            </button>
          </div>
          <div v-for="p in profiles" :key="p.user_id || p.id" class="memory-card">
            <div class="memory-card-header">
              <div class="memory-card-title">
                <span v-html="$icons.user" style="width:16px;height:16px;display:inline-block;vertical-align:middle;margin-right:6px"></span>
                {{ p.user_id || p.id || '未知用户' }}
              </div>
              <div style="display:flex;align-items:center;gap:8px">
                <div class="memory-card-date">{{ formatTime(p.updated_at || p.last_update) }}</div>
                <button class="btn-icon" title="删除此用户画像" :disabled="deleting" @click="deleteProfiles(p.user_id)">
                  <span style="color:var(--error)">✕</span>
                </button>
              </div>
            </div>
            <div class="memory-card-content">
              <div v-for="(val, key) in p" :key="key"
                v-if="!['user_id','id','updated_at','last_update'].includes(key)"
                style="margin-bottom:4px;display:flex;gap:8px">
                <span style="color:var(--text-muted);min-width:80px;font-size:12px">{{ key }}:</span>
                <span style="color:var(--text-secondary);font-size:13px">{{ renderValue(val) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ── 情景记忆 ────────────────────────────────── -->
      <div v-show="section === 'episodic'">
        <!-- 加载 -->
        <div v-if="loading" class="loading-overlay">
          <div class="spinner"></div>
          <span>加载情景记忆...</span>
        </div>

        <!-- 错误 -->
        <div v-else-if="error" class="error-state">
          <p>{{ error }}</p>
          <button class="btn btn-secondary mt-8" @click="loadEpisodic">重试</button>
        </div>

        <!-- 空 -->
        <div v-else-if="episodic.length === 0" class="memory-empty">
          <div v-html="$icons.database" style="width:48px;height:48px;opacity:0.3;margin:0 auto 12px"></div>
          <p>暂无情景记忆数据</p>
        </div>

        <!-- 列表 -->
        <div v-else>
          <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
            <button class="btn btn-sm btn-danger" :disabled="deleting" @click="deleteEpisodic('')">
              <span v-if="deleting" class="spinner-sm"></span>
              <span v-else>🗑</span> 清除全部
            </button>
          </div>
          <div v-for="(item, i) in episodic" :key="i" class="memory-card">
            <div class="memory-card-header">
              <div class="memory-card-title">
                <span v-html="$icons.clock" style="width:16px;height:16px;display:inline-block;vertical-align:middle;margin-right:6px"></span>
                记忆 #{{ i + 1 }}
              </div>
              <div class="memory-card-date">{{ formatTime(item.timestamp || item.created_at) }}</div>
            </div>
            <div class="memory-card-content">
              <div v-if="item.content" style="margin-bottom:4px">{{ item.content }}</div>
              <div v-for="(val, key) in item" :key="key"
                v-if="!['content','timestamp','created_at'].includes(key)"
                style="margin-bottom:2px;display:flex;gap:8px">
                <span style="color:var(--text-muted);min-width:80px;font-size:12px">{{ key }}:</span>
                <span style="color:var(--text-secondary);font-size:13px">{{ renderValue(val) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
};
