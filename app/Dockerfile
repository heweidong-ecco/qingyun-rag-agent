# ==================== 第一阶段：构建阶段 ====================
FROM python:3.10-slim AS builder

# 设置工作目录
WORKDIR /app

# 安装构建依赖（gcc 等，如果有 C 扩展的包需要编译）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 在第一阶段（builder）里
#
# 🔴 2026-09-29：**镜像源从 aliyun 换成 tsinghua**（作者指示「换镜像源」）。
#
#    **为什么**：2026-09-29 在**容器内**实测（同一个包 `numpy==1.26.4`，同一条件）：
#
#      | 镜像                                | 速度            |
#      |-------------------------------------|-----------------|
#      | `pypi.tuna.tsinghua.edu.cn`（现用） | **17.4 MB / 6.0s → 2955 KB/s** |
#      | `pypi.org`（官方）                  | 17.4 MB / 7.2s → 2466 KB/s |
#      | ~~`mirrors.aliyun.com`（原用）~~    | 🔴 **120 秒超时，没下完** |
#
#    ⇒ **aliyun 是"能连但极慢"（索引页 200，真包下不动）**，实测 ~45 KB/s。
#      **这才是「build 要 15–20 分钟」的主因** —— 比 torch 那个还大。
#
#    ⚠️ **与「裁依赖」是两件事，都要做**（见下面那行 `grep -vE`）：
#      裁依赖省掉最大的一块（torch 554.6 MB）；换源让**剩下的 100+ MB** 快 ~65 倍。
#
#    ⚠️ **不动 `requirements.txt`** —— 这只改**构建期的下载源**，仓里依赖清单一份没动。
RUN mkdir -p /root/.pip && echo "[global]\nindex-url = https://pypi.tuna.tsinghua.edu.cn/simple/" > /root/.pip/pip.conf
# 将依赖安装到 /install 目录，用于后续复制
# --user 安装到用户目录，避免权限问题
#
# 🔴 2026-09-29：**构建期裁掉重排序依赖**（作者裁「甲」，）。
#
#    **为什么**：`requirements.txt:59` 的 `sentence-transformers` 会**连带拉入 `torch`** ——
#      实测 `torch-2.14.0-cp310-manylinux_2_28_x86_64.whl` = **554.6 MB**（另加 scipy 37.7 / gradio 31.4 …），
#      而本机下载速度波动极大（实测 24.8 MB/s → 1.3 MB/s）⇒ **一次 build 要 15–20 分钟**。
#
#    **而应用不需要它**：`app/rag/reranker.py:14` 是**真懒加载**
#      （`from sentence_transformers import CrossEncoder` 写在 `get_reranker()` **函数体内**），
#      只有 `mode=accurate/full` 与 `rerank_search` 才碰它 —— 默认 `accurate_norerank` 根本不 import。
#
#    ⇒ **这正是 「api（fast 模式，不装重排序）」那条**：
#      线上跑 2核4G，**重排序直接不做**（本机 8 GB 都跑不动它）。
#
#    ⚠️ **没有加第二份清单**（作者裁过「**不用双 requirements.txt，这样会混**」）——
#       这里只是**构建期过滤**，仓里**仍然只有一份 `requirements.txt`**。
#    ⚠️ **代价（已知并接受）**：镜像里**跑不了 `mode=accurate` / `rerank_search`**。
#       线上本来就标注「fast 模式；重排序未启用」（延续 README 的 🔻 调性）。
#       若将来要恢复：把这行换回 `pip install ... -r requirements.txt` 即可。
# 🔴 2026-09-29 扩到 **5 个包**（作者裁「乙」，）。
#
#    新增裁掉的 3 个，**都是【工具】，不是【功能】**（2026-09-29 实测三者的调用形态）：
#
#      | 包         | 形态        | 谁用它                                    |
#      |------------|-------------|-------------------------------------------|
#      | `locust`   | **外部 CLI** | **代码里 0 处 import**；靠 `locust -f locust/locustfile_hybrid.py` 跑 |
#      | `ragas`    | **库**      | **只被 `evaluate_with_ragas.py` import**（那个脚本自己就是入口） |
#      | `datasets` | **库**      | 同上                                      |
#
#    ⇒ `requirements.txt` 是 **API 运行时的依赖清单**；而**压测工具 / 评估库不属于 API**
#      ⇒ 它们出现在这份清单里，**本身就是分类错了**。这里只是**按用途把它挪出去**。
#
#    ✅ **实测确认安全**：裁完后**没有任何非评估/非测试文件 import 这 5 个包**。
#    ✅ **测试一条都不会增加** —— 没有新增任何入口（`evaluate_with_ragas.py` 照旧）。
#    ⚠️ **代价**：镜像里跑不了压测与 RAGAS 评估 —— 而那两件事**本来就跑在开发机上**。
RUN grep -vE '^(sentence-transformers|transformers|locust|ragas|datasets)' requirements.txt > /tmp/req-light.txt \
    && echo "--- 被裁掉的（应 5 行）---" \
    && grep -cE '^(sentence-transformers|transformers|locust|ragas|datasets)' requirements.txt \
    && echo "--- 剩余行数 ---" \
    && wc -l < /tmp/req-light.txt \
    && pip install --user --no-cache-dir -r /tmp/req-light.txt


# ==================== 第二阶段：运行阶段 ====================
FROM python:3.10-slim AS runtime

# 设置工作目录
WORKDIR /app

# 只安装运行时必需的库（不需要 gcc 等编译工具）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 从构建阶段复制已安装的 Python 包
COPY --from=builder /root/.local /root/.local

# 复制应用代码
COPY . .

# 将 pip 用户安装的包路径加入 PATH
ENV PATH=/root/.local/bin:$PATH

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]