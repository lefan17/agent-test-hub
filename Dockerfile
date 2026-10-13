# syntax=docker/dockerfile:1
#
# AgentTest Hub 运行镜像
#
# 设计要点：
#   1. 基础镜像 python:3.12-slim —— 与 CI（setup-python 3.12）及本机 .venv（3.12.10）一致，
#      避免「本地绿、容器红」的版本漂移。
#   2. 依赖装两批：
#        - 运行依赖 requirements.txt（被测系统 + Agent）
#        - 测试/工程化依赖 requirements-dev.txt（pytest-cov / mypy / ruff / pip-audit）
#      用 build ARG INSTALL_DEV 控制，默认 0（只装运行依赖），
#      测试用 service 在 compose 里传 --build-arg INSTALL_DEV=1。
#   3. 依赖层与源码层分离：先 COPY requirements*.txt 再 pip install，
#      最后才 COPY 源码 —— 改代码不会让依赖层缓存失效。
#   4. 不装 `-e .`：本仓库的 pytest 约定是 `python -m pytest`（把 WORKDIR 加进 sys.path[0]），
#      与 .github/workflows/test.yml 里记录的口径完全一致；
#      容器内裸 `pytest` 同样会 ModuleNotFoundError: No module named 'demo_app'。
#
# 未验证项：本沙箱 Docker daemon 无法启动（WSL E_ACCESSDENIED），
#          本文件只做过 `docker compose config --quiet` 静态校验，从未真实 build/run。
#          README「容器化」一节同样如此声明。

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./

ARG INSTALL_DEV=0

RUN python -m pip install --upgrade pip \
 && python -m pip install -r requirements.txt \
 && if [ "$INSTALL_DEV" = "1" ]; then python -m pip install -r requirements-dev.txt; fi

COPY . .

# 被测系统（demo_app）监听端口；须与 AGENTTEST_DEMO_URL 的默认端口一致
#
# ⚠️ AGENTTEST_BIND_HOST=0.0.0.0 是容器路径的必需项，不是可选优化：
#    demo_app/app.py 默认绑 127.0.0.1（容器内的回环），此时宿主侧
#    `-p 5001:5001` 转发进来的连接会被容器网络栈直接 reset，
#    表现为 `curl: (56) Recv failure: Connection reset by peer`。
#    这一条只在镜像里设置，本机 `python -m demo_app.app` 仍走 app.py 的
#    默认值 127.0.0.1 —— 容器外行为一个字节都没变。
ENV AGENTTEST_DEMO_URL=http://127.0.0.1:5001 \
    AGENTTEST_ENV=dev \
    AGENTTEST_BIND_HOST=0.0.0.0
EXPOSE 5001

# 默认入口：启动被测系统。用 `-m` 而非 `python demo_app/app.py`，
# 否则包内相对导入（from demo_app.rag import ...）会解析失败。
CMD ["python", "-m", "demo_app.app"]
