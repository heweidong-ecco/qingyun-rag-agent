# 代码执行器镜像
#
# ⚠️ **与 `app/Dockerfile` 是两件事，⛔ 别合并**：
#   · `app/Dockerfile` = 应用本体（langchain / 数据库驱动 / 一大堆）
#   · 本文件 = **一个只跑 `executor_server` 的极小镜像**
#
# 🔴 **为什么刻意做小**：这是**被攻破时损失最大**的那个容器 —— 它在跑**任意代码**。
#   ⇒ 镜像里**不许有**应用那一堆依赖、⛔ 不许有凭据、⛔ 不许挂任何卷。
#   「这个容器里有什么」= 「逃出去的人能拿到什么」。
#
# 🔴 **应用不碰 `docker.sock`** —— 它只发 HTTP 到本服务。
#   理由见 `app/tools/executor_server.py` 的模块 docstring（sock = 宿主 root 等价）。

FROM python:3.10-slim

# 只装跑这个服务**必需**的三个（fastapi 会带上 pydantic / starlette）。
# ⚠️ ⛔ **不许 `-r requirements.txt`** —— 那会把 langchain / torch 系 / 数据库驱动全拉进来。
# 镜像源与 `app/Dockerfile` 一致（tsinghua，理由见那边的实测表）。
RUN mkdir -p /root/.pip \
    && printf '[global]\nindex-url = https://pypi.tuna.tsinghua.edu.cn/simple/\n' > /root/.pip/pip.conf \
    && pip install --no-cache-dir fastapi uvicorn \
    && rm -rf /root/.cache

WORKDIR /app

# ⚠️ **只复制这两个文件** —— ⛔ 不 `COPY . .` / ⛔ 不复制整个 `app/`：
#    `app/` 里有应用的全部代码、`.env` 的可能落点、以及一堆与本服务无关的东西。
#    「最小复制」是**故意的**，⛔ 不是图省事。
#
# 🔴 **2026-10-15 修：路径必须带 `tools/`** —— 这两个文件在 `38b2805`
#    （「模块化目录 —— `app/` 拆模块组」）里**搬进了 `app/tools/`**，而**本文件漏改**。
#    ⇒ 后果：**任何人 clone 下来 `docker compose build executor` 都当场失败**
#      （`"/executor_server.py": not found`），即「**clone 下来就是完整一份**」有个洞。
#    ⚠️ **为什么是 `tools/` 而不是像原先那样拍平进 `/app/`**：
#      `executor_server.py` 里写的是 **`from tools.code_executor_impl import …`**
#      （与 `app/` 全仓口径一致）⇒ 拍平会让**导入当场失败**。
#    📌 而**沙箱子进程**那一侧仍然按**扁平**导入（`from code_executor_impl import …`）——
#      它自己会 `sys.path.insert(0, sys.argv[1])`（父进程传的是**本模块所在目录**）
#      ⇒ 两条导入路径**同时成立**，靠的正是保住 `tools/` 这一层。
#    📌 判据（可打印）：`docker compose build executor` ⇒ 退出码 **0**
#      （修之前：`failed to compute cache key: "/executor_server.py": not found`）
COPY tools/executor_server.py tools/code_executor_impl.py /app/tools/

# 运行时是【只读根文件系统】（见 `docker-compose.yml` 的 `executor` 服务）
# ⇒ ⛔ 不许写 `__pycache__`，否则一 import 就 `OSError: Read-only file system`。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ⚠️ 只为文档目的 —— ⛔ **compose 里不映射端口**：它只在内部网络里被 `api` 调。
EXPOSE 8000

CMD ["uvicorn", "tools.executor_server:app", "--host", "0.0.0.0", "--port", "8000"]
