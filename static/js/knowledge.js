/* ══════════════════════════════════════════════════════════════
   EchoMind — KnowledgeView 组件
   知识库管理：按文档分组 + 详情展开 + 文件上传去重提示
   ══════════════════════════════════════════════════════════════ */

const KnowledgeView = {
  name: 'KnowledgeView',

  setup() {
    const section    = ref('list');       // list | add | upload
    const stats      = reactive({ total_chunks: 0, bm25_docs: 0 });
    const documents  = ref([]);
    const loading    = ref(false);
    const error      = ref(null);
    const formOpen   = ref(false);
    const expanded   = reactive(new Set());  // 展开的 doc_id 集合

    // 新增文档表单
    const newDoc     = reactive({ title: '', content: '' });
    const adding     = ref(false);

    // 文件上传
    const uploadFile  = ref(null);
    const uploading   = ref(false);
    const dragOver    = ref(false);
    const uploadResult = ref(null);
    const fileInputRef = ref(null);

    const { $api, $toast, $icons: _ik } = Vue.getCurrentInstance().appContext.config.globalProperties;
    const toastIcons = {
      success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>',
      error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>',
      warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 22h20L12 2z"/><path d="M12 10v4M12 18h.01"/></svg>',
      info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
    };

    // ── 加载统计数据 ───────────────────────────────────────
    async function loadStats() {
      try {
        const data = await $api.kbStats();
        stats.total_chunks = data.total_chunks;
        stats.bm25_docs = data.bm25_docs;
      } catch (err) {
        console.warn('加载知识库统计失败:', err);
      }
    }

    // ── 加载文档列表（按文档分组） ─────────────────────────
    async function loadDocuments() {
      loading.value = true;
      error.value = null;
      try {
        const data = await $api.kbDocs();
        documents.value = data.documents || [];
        stats.total_chunks = data.total || stats.total_chunks;
      } catch (err) {
        // 降级到普通列表
        try {
          const data = await $api.kbList();
          // 按 title 手动分组
          const groups = {};
          for (const c of (data.chunks || [])) {
            const key = c.title || '未命名';
            if (!groups[key]) groups[key] = { title: key, doc_id: c.doc_id || key, chunk_count: 0, chunks: [] };
            groups[key].chunks.push(c);
            groups[key].chunk_count++;
          }
          documents.value = Object.values(groups).map(g => {
            g.content_preview = g.chunks[0]?.content?.slice(0, 120) + '…' || '';
            return g;
          });
          stats.total_chunks = data.total || stats.total_chunks;
        } catch (err2) {
          error.value = err2.message;
          $toast('error', `加载文档列表失败: ${err2.message}`);
        }
      } finally {
        loading.value = false;
      }
    }

    // ── 展开/收起 ─────────────────────────────────────────
    function toggleExpand(docId) {
      if (expanded.has(docId)) expanded.delete(docId);
      else expanded.add(docId);
      // 触发响应式
      expanded.size; // force tracking
    }

    function isExpanded(docId) {
      return expanded.has(docId);
    }

    // ── 切换标签页 ─────────────────────────────────────────
    function switchSection(s) {
      section.value = s;
      if (s === 'list') loadDocuments();
      if (s === 'list' && !stats.total_chunks) loadStats();
    }

    // ── 添加文档 ───────────────────────────────────────────
    async function addDocument() {
      if (!newDoc.title.trim() || !newDoc.content.trim()) {
        $toast('warning', '请填写标题和内容');
        return;
      }
      adding.value = true;
      try {
        const result = await $api.kbAdd([{
          title: newDoc.title.trim(),
          content: newDoc.content.trim(),
        }]);
        const dedupMsg = result.skipped > 0 ? `（跳过 ${result.skipped} 篇重复）` : '';
        $toast('success', `${result.message || '已添加'} ${dedupMsg}`);
        newDoc.title = '';
        newDoc.content = '';
        formOpen.value = false;
        await loadStats();
        await loadDocuments();
      } catch (err) {
        $toast('error', `添加失败: ${err.message}`);
      } finally {
        adding.value = false;
      }
    }

    // ── 文件上传 ───────────────────────────────────────────
    function onFileSelect(e) {
      const file = e.target.files?.[0];
      if (file) uploadFile.value = file;
    }

    function onDragOver(e) {
      e.preventDefault();
      dragOver.value = true;
    }

    function onDragLeave() {
      dragOver.value = false;
    }

    function onDrop(e) {
      e.preventDefault();
      dragOver.value = false;
      const file = e.dataTransfer?.files?.[0];
      if (file) uploadFile.value = file;
    }

    function cancelUpload() {
      uploadFile.value = null;
      uploadResult.value = null;
      if (fileInputRef.value) fileInputRef.value.value = '';
    }

    async function uploadFileAction() {
      if (!uploadFile.value) return;
      uploading.value = true;
      uploadResult.value = null;
      try {
        const data = await $api.kbUpload(uploadFile.value);
        const dedupMsg = data.skipped > 0 ? `（跳过 ${data.skipped} 篇重复）` : '';
        const msg = data.message || `文件已导入，新增 ${data.added_chunks} 个片段`;
        uploadResult.value = { type: 'success', message: `${msg} ${dedupMsg}` };
        cancelUpload();
        await loadStats();
        await loadDocuments();
      } catch (err) {
        try {
          const errData = JSON.parse(err.message);
          uploadResult.value = { type: 'error', message: `上传失败: ${errData.detail || '未知错误'}` };
        } catch {
          uploadResult.value = { type: 'error', message: `上传失败: ${err.message}` };
        }
      } finally {
        uploading.value = false;
      }
    }

    // ── 删除文档（按 doc_id） ─────────────────────────────────
    async function deleteDocument(docId, title) {
      if (!confirm(`确定要删除「${title || docId}」吗？`)) return;
      try {
        const result = await $api.kbDelete(docId);
        $toast('success', result.message || '已删除');
        await loadDocuments();
        await loadStats();
      } catch (err) {
        $toast('error', `删除失败: ${err.message}`);
      }
    }

    // ── 清空知识库 ─────────────────────────────────────────
    async function clearAll() {
      if (!confirm('⚠️ 确定要清空全部知识库吗？此操作不可恢复！')) return;
      try {
        const result = await $api.kbClear();
        $toast('success', result.message || '知识库已清空');
        await loadDocuments();
        await loadStats();
      } catch (err) {
        $toast('error', `清空失败: ${err.message}`);
      }
    }

    // ── 格式化文件大小 ─────────────────────────────────────
    function formatSize(bytes) {
      if (!bytes) return '';
      if (bytes < 1024) return bytes + ' B';
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
      return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    // ── 截断内容预览 ──────────────────────────────────────
    function contentPreview(text) {
      if (!text) return '';
      const clean = text.replace(/\n/g, ' ').replace(/\[表格开始\].*?\[表格结束\]/g, '[表格] ').trim();
      return clean.length > 80 ? clean.slice(0, 80) + '…' : clean;
    }

    // ── 初始加载 ─────────────────────────────────────────
    loadStats();
    loadDocuments();

    return {
      section, stats, documents, loading, error, formOpen,
      newDoc, adding, uploadFile, uploading, dragOver, uploadResult, fileInputRef, toastIcons,
      expanded, toggleExpand, isExpanded,
      loadDocuments, switchSection,
      addDocument, onFileSelect, onDragOver, onDragLeave, onDrop,
      uploadFileAction, cancelUpload, deleteDocument, clearAll,
      formatSize, contentPreview,
    };
  },

  template: `
    <div class="knowledge-view">
      <!-- 统计卡片 -->
      <div class="stats-row">
        <div class="stat-card">
          <div class="stat-value">{{ stats.total_chunks }}</div>
          <div class="stat-label">文档片段总数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.bm25_docs }}</div>
          <div class="stat-label">BM25 索引文档</div>
        </div>
      </div>

      <!-- 功能标签 -->
      <div class="section-tabs">
        <button :class="['section-tab', { active: section === 'list' }]" @click="switchSection('list')">文档列表</button>
        <button :class="['section-tab', { active: section === 'add' }]" @click="switchSection('add')">新增文档</button>
        <button :class="['section-tab', { active: section === 'upload' }]" @click="switchSection('upload')">上传文件</button>
      </div>

      <!-- ── 文档列表（按文档分组） ────────────────────── -->
      <div v-show="section === 'list'">
        <!-- 加载中 -->
        <div v-if="loading" class="loading-overlay">
          <div class="spinner"></div>
          <span>加载文档列表...</span>
        </div>

        <!-- 错误 -->
        <div v-else-if="error" class="error-state">
          <p>{{ error }}</p>
          <button class="btn btn-secondary mt-8" @click="loadDocuments">重试</button>
        </div>

        <!-- 空 -->
        <div v-else-if="documents.length === 0" class="memory-empty">
          <div v-html="$icons.database" style="width:48px;height:48px;opacity:0.3;margin:0 auto 12px"></div>
          <p style="color:var(--text-muted)">知识库暂无内容</p>
        </div>

        <!-- 文档卡片列表 -->
        <div v-else style="display:flex;flex-direction:column;gap:10px">
          <div style="display:flex;justify-content:flex-end">
            <button class="btn-sm btn-danger" @click="clearAll">
              <span v-html="$icons.trash"></span> 清空全部
            </button>
          </div>

          <div v-for="doc in documents" :key="doc.doc_id || doc.title" class="memory-card" style="cursor:default">
            <!-- 文档头部 -->
            <div style="display:flex;align-items:center;justify-content:space-between;cursor:pointer" @click="toggleExpand(doc.doc_id || doc.title)">
              <div style="display:flex;align-items:center;gap:8px;min-width:0">
                <span v-html="$icons.files" style="width:18px;height:18px;color:var(--accent);flex-shrink:0"></span>
                <span style="font-weight:500;color:var(--text-primary);font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ doc.title || '未命名' }}</span>
                <span style="color:var(--text-muted);font-size:12px;white-space:nowrap">{{ doc.chunk_count || doc.chunks?.length || 0 }} 个片段</span>
              </div>
              <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">
                <button class="btn-sm btn-danger" @click.stop="deleteDocument(doc.doc_id, doc.title)" title="删除整篇文档">
                  <span v-html="$icons.trash"></span> 删除
                </button>
                <span v-html="$icons.chevronDown"
                  :style="{ transform: isExpanded(doc.doc_id || doc.title) ? 'rotate(180deg)' : '', transition: 'transform 200ms', width:'18px', height:'18px', color:'var(--text-secondary)', display:'inline-block' }">
                </span>
              </div>
            </div>

            <!-- 内容预览 -->
            <div v-if="!isExpanded(doc.doc_id || doc.title)" style="margin-top:6px;font-size:13px;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              {{ doc.content_preview || (doc.chunks?.[0]?.content?.slice(0,120) + '…' || '') }}
            </div>

            <!-- 展开详情：所有 chunk -->
            <div v-if="isExpanded(doc.doc_id || doc.title)" style="margin-top:10px;border-top:1px solid var(--glass-border);padding-top:10px;display:flex;flex-direction:column;gap:8px">
              <div v-for="(chunk, ci) in (doc.chunks || [])" :key="chunk.id || ci"
                style="background:rgba(255,255,255,0.03);border-radius:8px;padding:10px 12px;font-size:13px">
                <div style="display:flex;justify-content:space-between;margin-bottom:4px">
                  <span style="color:var(--text-muted);font-size:11px">Chunk {{ chunk.chunk_index + 1 }} / {{ chunk.total_chunks || doc.chunk_count }}</span>
                  <span style="color:var(--text-muted);font-size:10px;font-family:monospace" :title="chunk.id">{{ chunk.id?.slice(0,12) || '' }}…</span>
                </div>
                <div style="color:var(--text-secondary);line-height:1.6;white-space:pre-wrap">{{ chunk.content }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ── 新增文档 ──────────────────────────────────── -->
      <div v-show="section === 'add'">
        <div class="form-section">
          <div :class="['form-section-header', { open: formOpen }]" @click="formOpen = !formOpen">
            <h3>添加文档</h3>
            <span v-html="$icons.chevronDown"></span>
          </div>
          <div :class="['form-section-body', { open: formOpen || !newDoc.title && !newDoc.content }]">
            <div class="form-group">
              <label>标题</label>
              <input v-model="newDoc.title" class="form-input" placeholder="文档标题" maxlength="200" />
            </div>
            <div class="form-group">
              <label>内容</label>
              <textarea v-model="newDoc.content" class="form-input form-textarea" placeholder="文档内容..." rows="6"></textarea>
            </div>
            <button class="btn btn-primary" @click="addDocument" :disabled="adding || !newDoc.title.trim() || !newDoc.content.trim()">
              <span v-if="adding" class="spinner" style="width:16px;height:16px;border-width:2px"></span>
              <span v-else v-html="$icons.plus"></span>
              {{ adding ? '添加中...' : '添加到知识库' }}
            </button>
            <span v-if="false" class="hint" style="display:block;margin-top:4px">内容相同的文档会自动去重跳过</span>
          </div>
        </div>
      </div>

      <!-- ── 上传文件 ──────────────────────────────────── -->
      <div v-show="section === 'upload'">
        <div class="form-section">
          <div style="padding:20px">
            <!-- 上传区域 -->
            <label
              :class="['upload-zone', { dragover: dragOver }]"
              @dragover.prevent="onDragOver"
              @dragleave.prevent="onDragLeave"
              @drop.prevent="onDrop"
              for="file-upload-input"
            >
              <template v-if="!uploading && !uploadFile">
                <div v-html="$icons.upload"></div>
                <p>拖放文件到此处，或点击选择</p>
                <div class="hint">支持 PDF / DOCX / JSON / TXT &middot; 最大 10MB &middot; 同名内容自动去重</div>
              </template>
              <template v-else-if="uploading">
                <div class="spinner" style="width:36px;height:36px;border-width:3px;margin:0 auto 8px"></div>
                <p style="color:var(--accent)">{{ uploadFile?.name || '上传中...' }}</p>
                <div class="hint">正在解析并导入知识库...</div>
              </template>
              <template v-else-if="uploadFile">
                <div v-html="$icons.upload" style="color:var(--accent)"></div>
                <p style="color:var(--accent)">{{ uploadFile.name }}</p>
                <div class="hint">{{ formatSize(uploadFile.size) }} &middot; 即将自动上传</div>
              </template>
            </label>
            <input
              ref="fileInputRef"
              id="file-upload-input"
              type="file"
              accept=".pdf,.docx,.json,.txt"
              @change="onFileSelect"
              style="display:none"
            />

            <!-- 上传进度/结果 -->
            <div v-if="uploadFile && !uploading" style="margin-top:14px;display:flex;align-items:center;gap:12px">
              <div style="flex:1;font-size:13px;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                <span style="color:var(--accent)">{{ uploadFile.name }}</span>
                &middot; {{ formatSize(uploadFile.size) }}
              </div>
              <button class="btn btn-primary" @click="uploadFileAction" style="white-space:nowrap">
                <span v-html="$icons.upload"></span> 开始上传
              </button>
              <button class="btn btn-secondary" @click="cancelUpload" style="white-space:nowrap">
                取消
              </button>
            </div>

            <!-- 上传结果提示 -->
            <div v-if="uploadResult" :class="['toast', uploadResult.type]" style="margin-top:12px;box-shadow:none;max-width:none">
              <span v-html="toastIcons[uploadResult.type]"></span>
              <span>{{ uploadResult.message }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
};
