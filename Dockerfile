# EchoMind 智能客服系统 — Docker 多阶段构建
# 目标：生产镜像尽量精简，开发镜像包含调试工具
# BGE-base-zh-v1.5 模型（~1.1GB）在构建时预下载，避免运行时首次下载超时

# ── 阶段 1：基础环境 ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

# curl 用于健康检查
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── 阶段 2：安装 Python 依赖 ──────────────────────────────────────────────────
FROM base AS dependencies

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# 预下载 BGE-base-zh-v1.5 嵌入模型（~1.1GB），避免运行时下载超时
# 使用 sentence-transformers 自动从 HuggingFace Hub 下载并缓存
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('BAAI/bge-base-zh-v1.5', device='cpu')"

# ── 阶段 3：生产镜像 ──────────────────────────────────────────────────────────
FROM base AS production

# 从依赖阶段复制已安装的 Python 包
COPY --from=dependencies /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin

# 复制预下载的 HuggingFace 模型缓存（BGE 模型）
COPY --from=dependencies /root/.cache/huggingface /home/echomind/.cache/huggingface

# 复制应用代码
COPY . .

# 创建必要目录
RUN mkdir -p /app/data/models /app/data/dict /app/data/eval /app/logs /app/config

# 非 root 用户运行
RUN useradd -m -u 1000 echomind && \
    chown -R echomind:echomind /app && \
    chown -R echomind:echomind /home/echomind/.cache
USER echomind

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── 阶段 4：开发镜像 ──────────────────────────────────────────────────────────
FROM dependencies AS development

COPY . .

RUN mkdir -p /app/data/models /app/data/dict /app/data/eval /app/logs /app/config /app/tests && \
    chmod -R 777 /app/data /app/logs

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
