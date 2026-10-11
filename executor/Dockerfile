# jwave-executor 镜像 · 纯 pip 构建（2026-08-18 采纳）
#
# 历史沿革：
# - v1（旧）：268MB 私有打包文件 jwave-env.tar.gz 解压（不可复现，文件不在 git）
# - v2（组员 07fcdc2）：Miniconda 从零构建（版本透明，但镜像大、构建 10-30min）
# - v3（本版，2026-08-18 验证后采纳）：python:3.11-slim + 纯 pip
#   实测：构建 30s、镜像 1.65GB、VAL-1 基准 3/3 PASS 且误差与旧环境逐项一致（物理等价）
#   权威版本（2026-10-08 声明对齐）：jax 0.4.38(cpu) / numpy 1.26.4 / scipy 1.14.1 / matplotlib 3.9.4 / jwave 0.2.1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    MPLCONFIGDIR=/tmp/mpl \
    LD_LIBRARY_PATH=/usr/local/lib \
    JAX_PLATFORMS=cpu \
    XLA_PYTHON_CLIENT_PREALLOCATE=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

# 核心科学栈（pip，预编译 wheel）；equinox 0.11.11 要求 jax>=0.4.38，显式对齐已验证环境
RUN pip install --no-cache-dir --timeout 120 --retries 10 \
        "jax[cpu]==0.4.38" \
        numpy==1.26.4 \
        scipy==1.14.1 \
        matplotlib==3.9.4

# jwave 0.2.1 及其运行时依赖（jwave 用 --no-deps 避免干扰 jax 解析）
RUN pip install --no-cache-dir \
        jaxdf==0.2.8 \
        equinox==0.11.11 \
        jaxtyping==0.2.37 \
        plum-dispatch==2.9.0 \
        beartype \
        wadler-lindig \
        rich \
    && pip install --no-cache-dir --no-deps jwave==0.2.1

WORKDIR /app
COPY executor.py /app/executor.py
USER 10001:10001
CMD ["python", "/app/executor.py"]
