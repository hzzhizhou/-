# 智能售后客服系统后端镜像（FastAPI + LangChain + Chroma）
# 构建：docker compose build（或 docker build -t rag-server .）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# slim 镜像不含 tzdata，不装则 TZ 环境变量不生效（容器内时间与宿主机差 8 小时）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装 CPU 版 torch（pip 默认会拉取捆绑 CUDA 依赖的巨型 wheel，镜像会多出数 GB），
# 再装其余依赖；requirements 不变时这两层走构建缓存不重装
COPY requirements.txt .
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

# 再拷业务代码（.dockerignore 已剔除 .env / 生成物 / 与运行时无关的目录）
COPY . .

EXPOSE 8000

# /health 全部关键依赖可用才返回 200（MySQL/向量库异常时 503）
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# 容器入口：向量库为空且语料目录非空时先自动入库（DashScope embedding，约 1-2 分钟），再起服务
CMD ["python", "docker/entrypoint.py"]
