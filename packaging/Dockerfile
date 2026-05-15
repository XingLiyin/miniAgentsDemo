# Stage 1: 构建前端
FROM node:20-slim AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: 运行时镜像
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libfontconfig1 \
        libfreetype6 \
        libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# 先装依赖（利用 Docker 层缓存，代码变了不用重新装包）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# 复制应用代码
COPY app/ ./app/
COPY resources/ ./resources/
COPY _run.py ./

# 从 Stage 1 拷入编译好的前端
COPY --from=frontend-builder /frontend/dist ./frontend/dist

EXPOSE 15926

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:15926/health')"

CMD ["uv", "run", "python", "_run.py"]
