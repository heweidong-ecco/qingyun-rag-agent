"""
API v1 路由集中定义
所有 /api/v1 前缀的接口在此管理。
"""
import json
# 2026-10-04 删 `import time`：本文件**只有** `/rag/async_ask` 与
#    `/rag/parallel_ask` 用 `time.time()`，两条端点已删（全仓已核，见 `grep -n '\btime\b'`）。
import uuid
from loguru import logger
from typing import Literal
from fastapi import APIRouter, Depends, Path, Query

# ①b：LLM 的唯一构造落点（`model` / `api_key` / `base_url` / `max_tokens` 都收在那一处）。
# `llm_factory` 模块级只 import `os`（langchain 是在 `make_llm` **函数内**才 import）
# ⇒ 放文件头**不破坏**本文件「导入期不拉 langchain」的既有做法（原 `MAX_TOKENS_ANSWER` 同理）。
from core.llm_factory import make_llm
from core.exceptions import ErrorCode, AppException
# B8 接线（①b）：会话级 token 上限。
# `token_tracker` 模块级只 import `os/json/threading` 等标准库 + `token_config`
#    （`db` / `langchain` 都是**函数内**惰性导入）⇒ 放文件头**不破坏**本文件
#    「导入期不拉 langchain」的既有做法（与上面 `MAX_TOKENS_ANSWER` 同一条理由）。
from billing.token_tracker import check_session_token_budget, record_from_response, usage_summary
# B11（①b）：全站日级熔断。与 B8 并列，别合并（B8 按会话 / B11 按全站）。
# breaker 只在**函数内**惰性导入 token_tracker ⇒ 放文件头不破坏本文件
#    「导入期不拉重依赖」的既有做法（同上面 `token_tracker` 那条注释的道理）。
from billing.breaker import circuit, global_key
from routing.schemas import (
    ChatHistoryResponse,   # 2026-10-13：PENDING 那三条欠账之一
    QuestionRequest,
    DocumentInsert,
    BatchDocumentInsert,
    RagSearchResponse,
    DocListResponse,      # N35：列出文档（2026-10-12）
    DocFullResponse,      # N35：取整篇全文
    DocInsertResponse,    # 单条入库的回执
    BatchInsertResponse,  # 批量入库的回执
    UploadDocResponse,    # 上传入库的回执
    DocDeleteResponse,    # 删除的回执（N58：前端在用它返回里的 id）
)
# 2026-10-04 删 `get_current_user_jwt`：它在本文件**只有** `jwt_ask_question` 一处用，
#    随该端点一起删 ⇒ 留着就是没人用的 import。
# `require_admin` 原先挂在这里、**本文件从未用过**（的「先挂起」那批）。
#    2026-10-07 清存量时**已删** —— 同批删掉的还有：`JSONResponse` · `ACCESS_TOKEN_EXPIRE_MINUTES`
#    `LoginRequest`/`RefreshRequest`/`UserCreate` · `insert_batch_documents` · `get_weather`
#    `append_chat_history`/`INTERRUPTED_SUFFIX` · `agent_graph`。**没删任何模块**，只删导入行。
from routing.deps import get_current_user_hybrid
#：WebSocket 的首帧认证依赖 —— 它与上面那两个**不能互换**（浏览器 WS
# 不能自定义请求头，`X-API-Key` / `Authorization` 送不上来）。
from routing.deps import require_ws_user
from core.db import get_db, insert_document, PUBLIC_OWNER, enable_ann_iterative_scan  # PUBLIC_OWNER：列表/取全文的作用域（N35）· enable_ann_iterative_scan：
from core.config import MAX_INGEST_CHUNKS, MAX_UPLOAD_BYTES  # 2026-10-14（①b）：字节上限 + 块数上限
from core.db import search_similar  # 2026-10-03 乙段：stream_search 改走共享层
from rag.embedding_client import get_embedding
# （2026-10-08 ·  §六·1）：检索链也要建轨迹 —— 见 `stream_search` 里那两处调用。
from tools.tool_visualizer import start_trace, finish_trace

from access.permission import get_user_role, UserRole  # 2026-10-03 删 `get_user_quota`（本文件从未使用；该函数已随  一起删）
from core.db import invalidate_bm25_cache
from rag.hybrid_search import hybrid_search
from rag.hybrid_search import rerank_search
from rag.hybrid_search import hybrid_search_with_rewrite
# 2026-09-20 删（D1/pyflakes 报 redefinition）：这一行与下方（`SearchMode` 那段附近）
#    的导入**重复**，且下方那份还多带 `create_accurate_norerank_pipeline`。
#    实测：本行那份在下方覆盖之前**从未被使用** ⇒ 删本行、保留下方更全的那份。
from fastapi import File, UploadFile
import tempfile

from rag.document_preprocessor import DocumentPreprocessor
from rag.chunker import CHUNK_CONFIGS, split_text_with_filter
from rag.document_parser import parse_document

# `INTERRUPTED_SUFFIX` 看着"没用"，但**必须留** —— 它是不是死导入，`ruff` 判不了：
#    `app/tests/test_cancel_propagation.py` **6 处**写作 `rag_mod.INTERRUPTED_SUFFIX`（**属性访问**，
#    不是 `from ... import`）。2026-10-07 清存量时**删过一次**，那 6 条用例当场全红
#    （`AttributeError: module 'api_v1_rag' has no attribute 'INTERRUPTED_SUFFIX'`）⇒ 已还原。
#    **这就是"F401 不等于死导入"的实例**：ruff 只看本文件的名字，看不见**别人按属性取**。
#    下面 import 行行尾那条 `noqa` 指令是**收尾动作**（2026-10-07，与本批清存量
#       同一刀，注释里不写那个井号，写了 ruff 会把它当成一条坏 noqa 指令打警告）：存量清零后
#       基线空了，本来可以把它留成基线的一条；**没有那样做** —— 基线的键是 `(文件, 规则)`，
#       挂一条 = 把 `app/routing/api_v1_rag.py` **所有** F401 一律放行（将来真加了死导入也不报）。
#       行尾 `noqa` 只放行**这一行**，且理由写在行边上。同理见 `app/agent/agent_checkpointer.py:31`。
from core.cache import get_chat_history, persist_turn, INTERRUPTED_SUFFIX  # noqa: F401


import os

router = APIRouter(prefix="/api/v1")


# ==================== 文档管理 ====================
# 新增功能，自动绑定当前用户
@router.post(
    "/rag/insert",
    response_model=DocInsertResponse,
    summary="插入单条文档",
    description="""
    接收一段文本，自动调用阿里百炼的 embedding 模型（`EMBEDDING_MODEL_MAIN`）转换为向量，
    然后存入 PostgreSQL(pgvector) 数据库。
    
    文档入库后即可被 `/rag/pg_search` 接口检索。
    
    **认证要求**：需要在请求头中携带 `X-API-Key` 或 `Authorization: Bearer <JWT>`。
    """,
    tags=["文档管理"],
    response_description="文档插入成功的确认信息，包含入库内容的摘要",
    responses={
        200: {"description": "文档插入成功"},
        401: {
            "description": "缺少或无效的 API Key / JWT",
            "content": {
                "application/json": {
                    "examples": {
                        "AUTH_MISSING": {
                            "summary": "缺少API Key",
                            "value": {
                                "error": "缺少API Key",
                                "code": "AUTH_MISSING",
                                "status_code": 401
                            }
                        },
                        "AUTH_EXPIRED": {
                            "summary": "凭证已过期",
                            "value": {
                                "error": "API Key无效或已过期",
                                "code": "AUTH_EXPIRED",
                                "status_code": 401
                            }
                        }
                    }
                }
            }
        },
        403: {
            "description": "无权限访问",
            "content": {
                "application/json": {
                    "examples": {
                        "error": "无权限访问",
                        "code": "FORBIDDEN",
                        "status_code": 403
                    }
                }
            }
        },
        429: {
            "description": "请求过于频繁或配额已用完",
            "content": {
                "application/json": {
                    "examples": {
                        "RATE_LIMITED": {
                            "summary": "触发限流",
                            "value": {
                                "error": "请求过于频繁，请稍后再试",
                                "code": "RATE_LIMITED",
                                "status_code": 429
                            }
                        },
                        "QUOTA_EXCEEDED": {
                            "summary": "配额已用完",
                            "value": {
                                "error": "今日调用次数已用完",
                                "code": "QUOTA_EXCEEDED",
                                "status_code": 429
                            }
                        }
                    }
                }
            }
        },
        500: {"description": "服务器内部错误（如 Embedding API 调用失败）","content": {"example": {"code": "INTERNAL_ERROR"}}}
    }
)
async def insert_single_doc(
    doc: DocumentInsert,
    user_name: str = Depends(get_current_user_hybrid),
):
    """插入单条文档，自动生成Embedding并入库"""
    # 🔴 （2026-10-16）：**入库不是对话链** —— 它没有"会话"这个概念
    #    ⇒ 给一个**显式**的非会话标记，让账本自己说清「这笔记的不是某个会话」。
    #    ⛔ **别让它静默落 `"system"`** —— 那个值的语义是「**连用户都没有**」（启动预热那种），
    #    而这里**用户是真的**（`user_name` 就是）。
    embedding = get_embedding(doc.content, user_name=user_name, thread_id="ingest")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (content, source, embedding, requested_by) VALUES (%s, %s, %s::vector, %s)RETURNING id",
                (doc.content, doc.source, embedding, user_name)
            )
            new_id = cur.fetchone()[0]
            conn.commit()
    # 文档已变更 → 必须**在函数内**失效 BM25 进程内缓存。
    # (原先写在模块级 = 只在 import 时执行一次 ⇒ 插入后缓存不失效,新文档在 BM25 通路里
    #  "不存在",必须重启进程才能检索到。2026-09-11 实测:重启前新文档不在 top10,重启后第 2 名)
    invalidate_bm25_cache()
    return {"status": "inserted","id": new_id, "content": doc.content[:100], "source": doc.source,"requested_by":user_name}

@router.post(
    "/rag/insert_batch",
    response_model=BatchInsertResponse,
    summary="批量插入文档",
    description="""
    批量插入文档，自动调用阿里百炼的 embedding 模型（`EMBEDDING_MODEL_MAIN`）转换为向量，
    然后存入 PostgreSQL(pgvector) 数据库。
    
    文档入库后即可被 `/rag/pg_search` 接口检索。
    
    **认证要求**：需要在请求头中携带 `X-API-Key` 或 `Authorization: Bearer <JWT>`。
    """,
    tags=["文档管理"],
    response_description="文档插入成功的确认信息，包含入库内容的摘要",
    responses={
        200: {"description": "文档插入成功"},
        401: {
            "description": "缺少或无效的 API Key / JWT",
            "content": {
                "application/json": {
                    "examples": {
                        "AUTH_MISSING": {
                            "summary": "缺少API Key",
                            "value": {
                                "error": "缺少API Key",
                                "code": "AUTH_MISSING",
                                "status_code": 401
                            }
                        },
                        "AUTH_EXPIRED": {
                            "summary": "凭证已过期",
                            "value": {
                                "error": "API Key无效或已过期",
                                "code": "AUTH_EXPIRED",
                                "status_code": 401
                            }
                        }
                    }
                }
            }
        },
        403: {
            "description": "无权限访问",
            "content": {
                "application/json": {
                    "examples": {
                        "error": "无权限访问",
                        "code": "FORBIDDEN",
                        "status_code": 403
                    }
                }
            }
        },
        429: {
            "description": "请求过于频繁或配额已用完",
            "content": {
                "application/json": {
                    "examples": {
                        "RATE_LIMITED": {
                            "summary": "触发限流",
                            "value": {
                                "error": "请求过于频繁，请稍后再试",
                                "code": "RATE_LIMITED",
                                "status_code": 429
                            }
                        },
                        "QUOTA_EXCEEDED": {
                            "summary": "配额已用完",
                            "value": {
                                "error": "今日调用次数已用完",
                                "code": "QUOTA_EXCEEDED",
                                "status_code": 429
                            }
                        }
                    }
                }
            }
        },
        500: {"description": "服务器内部错误（如 Embedding API 调用失败）","content": {"example": {"code": "INTERNAL_ERROR"}}}
    }
)
async def insert_batch(
    batch: BatchDocumentInsert,
    user_name: str = Depends(get_current_user_hybrid)  # 注入当前用户名
):
    """批量插入文档"""
    count = 0
    for doc in batch.documents:
        embedding = get_embedding(doc.content, user_name=user_name, thread_id="ingest")   # 🔴：同上
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO documents (content, source, embedding, requested_by) VALUES (%s, %s, %s::vector, %s)",
                    (doc.content, doc.source, embedding, user_name)
                )
                conn.commit()
        count += 1
    # 同上：批量插入后也须失效，否则本批新文档检索不到
    invalidate_bm25_cache()
    return {"status": "inserted", "count": count, "requested_by":user_name}

# 上传并解析复杂 PDF 文件
# 文档上传接口，在解析后、入库前进行预处理：
preprocessor = DocumentPreprocessor()

@router.post("/rag/upload_document", response_model=UploadDocResponse)
async def upload_document(
    file: UploadFile = File(...),
    domain: str = "default",  # 新增：用户可指定领域，默认为 "default"，法律"legal",医疗"medical"
    doc_type: str = "",       # 2026-10-08 新增：**可选**分块档位。
                              # 默认空 = **沿用扩展名推断** ⇒ **访客行为一字不变**；
                              #    灌库脚本（乙的四类语料）走这条显式指定。
    user_name: str = Depends(get_current_user_hybrid),
):
    """上传并解析多格式文档（PDF/Word/Markdown/HTML），经过预处理后入库。

    2026-09-20 修：此处原有**两行紧挨着的 docstring** ——
       第二行（那句「上传并解析多格式文档，经过预处理后入库」，用三引号包着）是**空操作**
       （函数已有一行 docstring）。已把它的信息并入第一行、删掉第二行。
    """
    # 检查文件格式
    allowed_extensions = ["pdf", "docx", "md", "html"]
    ext = file.filename.lower().split(".")[-1]
    if ext not in allowed_extensions:
        raise AppException(
            ErrorCode.PARAM_INVALID,
            f"不支持的文件格式: {ext}，支持: {', '.join(allowed_extensions)}"
        )
    
    # 保存临时文件
    # 2026-09-20 删（D1/pyflakes 报 redefinition）：这里原先又 `import tempfile` / `import os`，
    #    而两者**模块级早就导入过**（见文件头部）⇒ 这两行只是把同名对象再绑一次，纯冗余。
    #
    # 🔴🔴 2026-10-14（`①b`  ·  §二·4.2 ①）：**大小上限必须卡在 `read()` 之前**
    #    改前那一句是 `tmp.write(await file.read())` —— **整个文件一次性进内存**
    #    ⇒ **"读完再判大小"等于没判**：内存已经吃掉了。
    # ⚠️ **为什么这条不是"锦上添花"**：本服务容器实测 **8 GiB 且 `SwapTotal=0`**
    #    ⇒ **OOM 直接 kill，没有缓冲**；**一个 200 MB 的文件就能打死容器，不需要恶意、不需要并发**。
    # ⇒ 做法：**边读边计，一超就停**（⛔ 不是先读完再 `len()`）。
    # ⚠️ 上限 **48 KB**（`config.MAX_UPLOAD_BYTES`，反推自"每次 1 万 token"）——
    #    ⛔ **不是 MB 级**，理由写在那个常量自己的注释里。
    # ⚠️ 先给个名字：`with` 里那句万一就抛了，下面 `finally` 读它会 `NameError`
    tmp_path = None
    try:
        total_bytes = 0
        _CHUNK = 64 * 1024          # 一次读多少：比上限大 ⇒ 单块就够判超，但不会一口气读完整份
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
            tmp_path = tmp.name
            while True:
                piece = await file.read(_CHUNK)
                if not piece:
                    break
                total_bytes += len(piece)
                if total_bytes > MAX_UPLOAD_BYTES:
                    # ⚠️ 用 `PARAM_INVALID`(422) 而不是 `QUOTA_EXCEEDED`(429)：
                    #    429 是在说"你**稍后**再来"，而**换一份小文件也照样不行**
                    #    ⇒ 那会把人引到错误的方向（没规定用哪个码）。
                    # ⚠️ 与上面那条"格式不支持"**同一个码、同一个 422** —— 两处同级。
                    os.unlink(tmp_path)        # ⚠️ 先清掉自己刚建的那个临时文件再抛
                    raise AppException(
                        ErrorCode.PARAM_INVALID,
                        f"文件过大（>{MAX_UPLOAD_BYTES // 1024} KB）—— "
                        f"本服务单次上传的额度是 {MAX_UPLOAD_BYTES // 1024} KB",
                    )
                tmp.write(piece)

        # 解析文档
        text = parse_document(tmp_path)
        # 根据领域预处理
        preprocessor = DocumentPreprocessor(domain=domain)
        # 预处理：清洗、去重、标准化
        cleaned_text = preprocessor.process(text)  # 在文件中，不会在中间件NLP全局中传导被读取← 这里要显式调用

        # 分块
        # 可选：对块进行语义去重
        # chunks = preprocessor.deduplicate_chunks(chunks)
        # 分块（根据文件格式选择分块策略）
        # PDF 和 Word 通常为技术文档，Markdown 也按技术文档处理
        #
        # 2026-10-08 改—— 原来只有下面这一句【按扩展名猜】：
        #        doc_type = "legal" if ext == "pdf" else "technical"
        #    ⇒ **除 PDF 外的一切全吃 `technical`** ⇒ `report`/`article`/`faq` 三档**从来用不上**。
        #    现在：**调用方给了就用**（乙的灌库脚本按类别显式给），**没给才回落到原来的推断**
        #    ⇒ **访客走的那条路一字不变**（作者 2026-10-08 裁的就是这个默认值）。
        doc_type = doc_type.strip() or ("legal" if ext == "pdf" else "technical")

        # 未知档位 ⇒ **当场拒绝**，不许往下走。
        #    理由：`chunker.get_text_splitter` 是 `CHUNK_CONFIGS.get(doc_type, default)`
        #    ⇒ **静默回落** `default` ⇒ 一个拼错的档名会让整批语料按 500/50 切，**不报任何错**。
        #    这一步是**本 Agent 补的**（§二）—— 作者原话只说"加可选形参"。
        if doc_type not in CHUNK_CONFIGS:
            raise AppException(
                ErrorCode.PARAM_INVALID,
                f"未知的 doc_type: {doc_type}，可选: {', '.join(sorted(CHUNK_CONFIGS))}",
            )
        chunks = split_text_with_filter(cleaned_text, doc_type=doc_type, min_length=20)

        # 🔴 2026-10-14（`①b`  ·  §二·4 第 3 条）：**单次块数上限**
        #    —— 与上面那道字节上限是**两个量纲**（字节管**花多少钱**，块数管**耗时/超时**）。
        # ⚠️ **必须在【入库循环之前】判** —— 进去之后就是逐块调 embedding 了，
        #    而 `Swap=0` 的容器上"跑到一半才超时"比"当场拒掉"糟得多。
        if len(chunks) > MAX_INGEST_CHUNKS:
            raise AppException(
                ErrorCode.PARAM_INVALID,
                f"文件切出来 {len(chunks)} 块，超过单次上限 {MAX_INGEST_CHUNKS} 块 —— "
                f"请拆小一点再传",
            )


        # 入库
        for chunk in chunks:
            embedding = get_embedding(chunk, user_name=user_name, thread_id="ingest")     # 🔴：同上
            insert_document(chunk, file.filename, embedding, user_name)  # user_name 作为 requested_by

        # 2026-10-12 补：**文档已变更 ⇒ 必须失效 BM25 进程内缓存**。
        #    另两条入库路径（`insert_single_doc` / `insert_batch`）**一直都有**这一句，
        #       只有**上传这条漏了** —— 而漏了**不会有任何报错**：
        #       混合检索里 BM25 那一半**看不见新块**，直到进程重启。
        #    这是**真被看见**的：`kb` 页的「入库完当场验一次」实测报「没召回」，
        #       而库里确实有、向量那半边也查得到 —— 就是这里缺一句。
        #    同族的前科写在 `insert_single_doc` 里（"原先写在模块级 = 只在 import 时执行一次"）。
        invalidate_bm25_cache()

    
        return {
            "filename": file.filename,
            "format": ext,
            "domain": domain,
            "chunks_inserted": len(chunks),
            "text_length": len(cleaned_text),
            "preview": cleaned_text[:500],
            "requested_by": user_name,
        }
    finally:
        # 🔴 2026-10-14（`①b`  ·  §二·4.2 ②）：**临时文件必须在 `finally` 里清**。
        #    改前那句 `os.unlink(tmp_path)` 在**函数末尾** ⇒ 中间任何一步抛异常
        #    （解析失败 / embedding 失败 / 上面那条超限拒绝）都**执行不到它**
        #    ⇒ `/tmp` 里攒垃圾，而 服务跑久了**没人会注意到**。
        #    ⚠️ `os.path.exists` 不是多余的：`with` 那一步本身失败时，文件根本没建出来。
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

# 🔴 （2026-10-16）：**本条一直缺 `response_model`，而前端真在用它的返回。**
#    漏的两半：① 这里没声明；② 那条门**认不出它** —— 删除的 URL 是前端**运行时拼的**
#    （`kb.js` 的 `FULL_PATH + id` · `kb.html` 的 `'/api/v1/rag/documents/' + id`），
#    字面量只到引号为止 ⇒ 被当成**列表端点**那条路径（而它**有** schema）⇒ 空过。
#    ⇒ 本批两半一起修：这里补模型，`app/tests/test_response_model_declared.py` 补两个洞。
@router.delete("/rag/documents/{doc_id}", response_model=DocDeleteResponse, tags=["文档管理"])
async def delete_document(
    doc_id: int = Path(..., ge=1, description="要删除的文档ID"),
    user_name: str = Depends(get_current_user_hybrid),
):
    """删除指定文档（只能删除自己上传的，管理员可删除所有）"""
    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. 先查询文档的归属
            cur.execute("SELECT requested_by FROM documents WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            if row is None:
                raise AppException(ErrorCode.RESOURCE_NOT_FOUND, f"文档 {doc_id} 不存在")
            
            owner = row[0]
            
            # 2. 权限判断：管理员 或 文档上传者本人
            role = get_user_role(user_name)
            if role != UserRole.ADMIN and owner != user_name:
                raise AppException(ErrorCode.FORBIDDEN, "只能删除自己上传的文档")
            
            # 3. 执行删除
            cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
            conn.commit()
    
    # 同上：删除后也须失效，否则已删文档仍会出现在 BM25 召回里
    invalidate_bm25_cache()
    return {"status": "deleted", "id": doc_id, "requested_by": user_name}

# ==================== 列出文档 + 取整篇（2026-10-12）====================
# 补的是：**全仓此前没有任何"列出文档"的端点** ⇒ `/kb` 只能做成
#    「传一条 ⇒ 回你 id ⇒ 拿 id 删」的闭环，而**访客看不见自己库里有什么**。
# 作者 2026-10-12 定的目的（逐字）：「让大部分不想自己提交文档的访客，
#    能知道已有哪些是已有的，是可以直接搜索，我该搜索什么，会出现内容」—— 即**可发现性**。
# 作用域：`mine`（默认）只看自己 · `public` 只看公用语料·
#    **不许把别人的列出来**。
CATEGORY_NAMES = {
    "A": "产品 / 规格", "B": "流程 / 制度", "C": "常见问题",
    "D": "数据 / 报告", "E": "非知识（负样本）",
}


def _category_of(source: str) -> str:
    """类别**从文件名前缀派生**（`A-01-…` ⇒ A）。

    表里**没有"目录"这一层** —— `documents.source` 存的只是**文件名**。
    ⇒ 「按文件夹逐级分层」是**做不到**的（硬做就是**编一层结构**出来）。
    这里用的 A–E 五类 ⇒ 依据是**语料方自己定的**（`testdata/corpus/README.md`），不是我编的。
    """
    head = (source or "").strip()[:1].upper()
    return CATEGORY_NAMES.get(head, "其他")


@router.get("/rag/documents", response_model=DocListResponse, tags=["文档管理"])
async def list_documents(
    scope: str = Query("mine", pattern="^(mine|public)$"),
    limit: int = Query(200, ge=1, le=500),
    user_name: str = Depends(get_current_user_hybrid),
):
    """列出**某个作用域**里的文档 —— **一行 = 一篇**（按 `source` 聚过）。

    **为什么按 source 聚**：表里存的是**切块**，一篇文档是**很多行**。
       直接列出来访客会看见「同一篇重复几十次」——那不是他要知道的。
    **`truncated` / `has_more` 必须回**（收了 `limit` 就有这道门）。
    """
    owner = PUBLIC_OWNER if scope == "public" else user_name
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, count(*) AS chunks, min(id) AS first_id,
                       (array_agg(content ORDER BY id))[1] AS preview
                FROM documents
                WHERE requested_by = %s
                GROUP BY source
                ORDER BY source
                LIMIT %s
                """,
                (owner, limit + 1),
            )
            rows = cur.fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]

    buckets = {}
    for source, chunks, first_id, preview in rows:
        cat = _category_of(source)
        buckets.setdefault(cat, []).append({
            "source": source, "category": cat, "chunks": int(chunks),
            "first_id": int(first_id), "preview": (preview or "")[:120],
        })
    order = [k for k in CATEGORY_NAMES.values() if k in buckets] +             (["其他"] if "其他" in buckets else [])
    groups = [{"name": k, "count": len(buckets[k]), "docs": buckets[k]} for k in order]
    return {
        "scope": scope, "total": len(rows), "groups": groups,
        "truncated": truncated, "has_more": truncated,
    }


@router.get("/rag/documents/{doc_id}", response_model=DocFullResponse, tags=["文档管理"])
async def get_document_full(
    doc_id: int,
    user_name: str = Depends(get_current_user_hybrid),
):
    """取**整篇**的全文。

    **`doc_id` 传该篇的【任意一个 chunk id】** —— 返回的是**整篇**，
       不是那一条：全文 = 该 `source` 的**全部 chunk 按 id 序拼起来**。
       这个语义有点反直觉，所以写在这里：列表里每行给的 `first_id` 就是干这个用的。
    **走 PostgreSQL 直接取文本**（不经向量库）—— 这正是作者要的那种"看文件内容"。
    越权面：**只许看自己的 + 公用语料**。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT source, requested_by FROM documents WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            if not row:
                raise AppException(ErrorCode.PARAM_INVALID, f"没有 id={doc_id} 的文档")
            source, owner = row
            if owner != user_name and owner != PUBLIC_OWNER:
                raise AppException(ErrorCode.FORBIDDEN, "只能看自己的，或公用语料")
            cur.execute(
                "SELECT content FROM documents WHERE source = %s AND requested_by = %s ORDER BY id",
                (source, owner),
            )
            chunks = [r[0] for r in cur.fetchall()]
    return {
        "source": source, "category": _category_of(source), "chunks": len(chunks),
        "content": "\n\n".join(chunks), "requested_by": owner,
    }

# ==================== 对话历史的**读回** ====================
# 2026-10-11（前端整体调整 · 批 5）：**这条端点补的是一个真缺陷** ——
#    在这之前**全仓没有任何"读回对话历史"的出口**（`get_chat_history` 只被服务端用来喂模型），
#    而对话页**从来没做过回显** ⇒ 访客换个页面再回来，**页面上对话是空的、而模型还记得**
#    ⇒ 它自己那句写着「本页不显示历史消息；这个会话的上下文模型仍然记得」。
#    **那不是创空间的限制**（那只是单容器）。作者 2026-10-11 实测报的：
#       「进行了一个对话，然后点执行轨迹，再点会话，原来的对话就没有了」。
#    设计 + 裁定 ⇒ `frontend/DES-整体调整-导航与运行区-20261011.md` §七。
@router.get("/rag/chat_history", response_model=ChatHistoryResponse, tags=["对话"])
async def get_thread_history(
    # **必填、无默认值**（同一条口径）：
    #    有默认值 ⇒ 「忘了传」会**静默读回另一个桶**，而页面上看着像"历史空了"。
    thread_id: str = Query(..., min_length=1, description="会话 id（要与提问时用的那个一致）"),
    user_name: str = Depends(get_current_user_hybrid),
):
    """读回**该用户在该会话下**的对话历史（契约 C 的那个键）。

    **只回当前用户 + 当前会话** —— 那正是 `session_key(user, thread)` 的用途；
    **不许做成"给个 id 就能读别人的"**（`user_name` 来自鉴权依赖，不是形参）。

    **窗口是【最近 5 轮】**（`get_chat_history` 的实现就是 `lrange(key, -10, -1)`）——
       那不是截断，是**喂给模型的那一段上下文**的长度。⇒ 响应里带上 `window` 说清它，
       别让页面把"更早的没回来"读成"丢了"。
    **半截回答**（取消 / 异常那次）**带着中断尾巴**存着——
       页面要能**看得出来**，别把它当完整答案渲染。
    """
    entries = get_chat_history(user_name, thread_id=thread_id)
    return {
        "thread_id": thread_id,
        "messages": entries,
        "count": len(entries),
        "window": "最近 5 轮",
        "requested_by": user_name,
    }


# ==================== 检索接口 ====================
# 新增：只检索 和 返回当前用户的文档
@router.post(
    "/rag/pg_search",
    summary="向量语义检索",
    description="使用阿里百炼 text-embedding-v2 将问题转为向量，在 pgvector 中检索最相关的文档块，返回按相似度降序排列的结果。",
    tags=["检索"],
    response_description="检索结果列表，包含文档内容、来源和相似度分数",
        responses={
        200: {"description": "检索成功"},
        401: {"description": "缺少或无效的 API Key", "content": {"example": {"code": "AUTH_MISSING"}}},
        403: {"description": "API Key 已过期", "content": {"example": {"code": "FORBIDDEN"}}},
        429: {
            "description": "请求过于频繁或配额已用完",
            "content": {
                "application/json": {
                    "examples": {
                        "RATE_LIMITED": {
                            "summary": "触发限流",
                            "value": {
                                "error": "请求过于频繁，请稍后再试",
                                "code": "RATE_LIMITED",
                                "status_code": 429
                            }
                        },
                        "QUOTA_EXCEEDED": {
                            "summary": "配额已用完",
                            "value": {
                                "error": "今日调用次数已用完",
                                "code": "QUOTA_EXCEEDED",
                                "status_code": 429
                            }
                        }
                    }
                }
            }
        },
        500: {"description": "服务器内部错误", "content": {"example": {"code": "INTERNAL_ERROR"}}}
    }
)
async def pg_search(
    req: QuestionRequest,
    thread_id: str = Query("default", min_length=1),   # 🔴：与同族 `/rag/hybrid_search` 同形
    user_name: str = Depends(get_current_user_hybrid)
):
    query_embedding = get_embedding(req.question, user_name=user_name, thread_id=thread_id)
    with get_db() as conn:
        with conn.cursor() as cur:
            enable_ann_iterative_scan(cur)      # 🔴：别让 ivfflat 静默回空
            cur.execute("""
                SELECT content, source, 1 - (embedding <=> %s::vector) AS similarity
                FROM documents
                WHERE requested_by = %s OR requested_by = %s   -- 自己的 + 公共语料
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
                """,
                (query_embedding, user_name, PUBLIC_OWNER, query_embedding, req.top_k))
            results = cur.fetchall()
    docs = [
        {
            "content": r[0],
            "source": r[1],
            "similarity": round(r[2], 4)
        }
        for r in results
    ]
    return {
        "question": req.question,
        "requested_by": user_name,
        "docs": docs,
        "embedding_dim": len(query_embedding)
    }


# 混合检索接口,加入BM25稀疏检索
@router.post("/rag/hybrid_search")
async def hybrid_search_api(
    req: QuestionRequest,
    thread_id: str = Query("default", min_length=1),   # 🔴 （2026-10-16）：本条原先【连形参都没有】
    user_name: str = Depends(get_current_user_hybrid),
):
    """混合检索：向量 + BM25 关键词（**只在该用户自己的文档内**）"""
    docs = hybrid_search(req.question, req.top_k, user_id=user_name, thread_id=thread_id)
    return {
        "question": req.question,
        "method": "hybrid (vector + bm25)",
        "docs": docs,
        "requested_by": user_name,
    }

# 添加重排序检索接口
@router.post("/rag/rerank_search")
async def rerank_search_api(
    req: QuestionRequest,
    thread_id: str = Query("default", min_length=1),   # 🔴：同 `/rag/hybrid_search`，原先也没有
    user_name: str = Depends(get_current_user_hybrid),
):
    """带重排序的混合检索（**只在该用户自己的文档内**）"""
    docs = rerank_search(req.question, req.top_k, user_id=user_name, thread_id=thread_id)
    return {
        "question": req.question,
        "method": "hybrid + RRF + Cross-Encoder Rerank",
        "docs": docs,
        "requested_by": user_name,
    }

# 新增带 用户查询问题改写 的检索函数。检索器：只使用了hybrid_search(bm25+vertor+RRF)
@router.post("/rag/rewrite_search")
async def rewrite_search_api(
    req: QuestionRequest,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),   # 2026-10-05 加（B8 需要会话维度）
    user_name: str = Depends(get_current_user_hybrid),
):
    """带查询改写的混合检索（**只在该用户自己的文档内**）"""
    # 2026-10-05：本端点**此前零闸** —— 而它**无条件**真调 LLM
    #     （`hybrid_search_with_rewrite` 里改写 + 扩展各一次）
    #     ① 单条请求**无上限** ② B8 会话上限 / B11 全站熔断读的计数器它从不写 ⇒ 对它等于不存在。
    #     **两道都要**，与 `/rag/stream_search` 的现状并列（B8 按会话 / B11 按全站，别合并）。
    ok, why = check_session_token_budget(user_name, thread_id)   # B8
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    ok, why = circuit(global_key())                              # B11
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    docs = hybrid_search_with_rewrite(req.question, req.top_k, user_id=user_name, thread_id=thread_id)
    return {
        "question": req.question,
        "method": "query rewrite + hybrid search + RRF",
        "docs": docs,
        "requested_by": user_name,
    }

# 添加综合检索接口 RAGPipeline
from rag.rag_pipeline import create_fast_pipeline, create_accurate_pipeline, create_accurate_norerank_pipeline,create_full_pipeline

# `mode` 必须是**受限枚举**，不能是裸 `str` —— 2026-09-17 修，起因见 `unified_search` 的注释。
SearchMode = Literal["fast", "accurate", "accurate_norerank", "full"]

# mode → 管线工厂。**用查表代替 if/elif/else**：漏一个 mode 会 KeyError（当场炸），
# 而不是静默落进某个兜底分支。
PIPELINE_FACTORIES = {
    "fast": create_fast_pipeline,
    "accurate": create_accurate_pipeline,
    "accurate_norerank": create_accurate_norerank_pipeline,
    "full": create_full_pipeline,
}


@router.post("/rag/search", response_model=RagSearchResponse)
async def unified_search(
    req: QuestionRequest,
    mode: SearchMode = "accurate_norerank",
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),   # 2026-10-05 加（B8 需要会话维度）
    user_name: str = Depends(get_current_user_hybrid),
):
    """
    综合检索入口，支持多种模式切换，返回各阶段耗时统计。

    **模式说明：**
    - **fast**：仅 BM25 + 向量检索 + RRF 融合，速度最快。
    - **accurate**：增加查询改写和 Cross-Encoder 重排序，精度最高。
    - **accurate_norerank**：增加查询改写，但**不做** Cross-Encoder 重排序（**默认值**，不依赖 torch）。
    - **full**：在 accurate 基础上增加查询扩展，覆盖最全。

    `mode` 是**受限枚举**，取值只有上面四个；传别的值会得到 **422**，而不是被静默兜底。
    """
    # 2026-10-05：本端点**此前零闸** —— 而默认 mode `accurate_norerank`
    #     **本身就开着改写**（真调 LLM）⇒ 单条请求无上限、账本也收不到数据。
    #     位置在 `PIPELINE_FACTORIES[mode]` **之前** —— 那是本端点第一处真花钱的地方之前。
    #     **两道都要**（B8 按会话 / B11 按全站），同 `/rag/stream_search` 的现状。
    ok, why = check_session_token_budget(user_name, thread_id)   # B8
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    ok, why = circuit(global_key())                              # B11
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why)

    # 2026-09-17 修：此处原先是一个**裸 `else`** —— 任何拼错的 mode（如 `fst`）
    # 都不报错，而是**静默换成 `accurate_norerank`**（多跑一次查询改写 = 多花钱、多延迟）。
    # 现在：`mode` 声明为 `SearchMode`（`Literal`），非法值由 FastAPI 在进入函数体之前挡成 422；
    # 且下方改用**查表**，结构上不存在"兜底分支"。决策。
    pipeline = PIPELINE_FACTORIES[mode]()

    # 执行检索
    result = await pipeline.search_async(
        req.question,
        top_k=req.top_k,
        conversation_history=req.conversation_history,  # 传递历史
        generate_answer=req.generate_answer,
        strict_mode=req.strict_mode,
        citations=req.citations,
        user_id=user_name,   # 2026-10-03 加—— 检索只在该用户自己的文档内
        # 🔴 （2026-10-16）：**这一行是本条缺陷的根源所在** ——
        #    上面 774 行那句 `check_session_token_budget(user_name, thread_id)` 用的是
        #    **调用者传的这个** `thread_id`，而在此之前它**从没往下传过**（管线连形参都没有）
        #    ⇒ 链上三处记账各自落 `"default"`/`"system"`/`"unknown"`
        #    ⇒ **闸查的桶恒空、恒放行**（实测：`get_session_token_usage` = 0）。
        thread_id=thread_id,
    )

    # 添加用户信息
    result["requested_by"] = user_name
    result["mode"] = mode

    return result


# ==================== 【已删除】POST /rag/jwt_ask ====================
# 2026-10-04 **删除**—— 端点 `jwt_ask_question` 已移除。三条理由（都可打印地核过）：
#
#   ① **拿到 `question` 却不拿它做检索** —— 无 embedding、无 `ORDER BY`，只是
#      `SELECT content … LIMIT n`。**`LIMIT` 配不上 `ORDER BY`** ⇒ 取哪几行由物理顺序决定
#      ⇒ **同一问题两次可能拿到不同的行**（"看着像检索、其实不是"—— 本仓反复记的那个形态）。
#   ② **能力被 `/rag/pg_search` 覆盖，而且是更严的覆盖** —— 它用 `get_current_user_jwt`，
#      **只收 JWT、不收 API Key**；而 `pg_search` 用 `get_current_user_hybrid`，
#      **JWT 与 API Key 都收** ⇒ 是它的**超集**。
#      **最容易读反的一点**：「只收 JWT」看着像独有能力，其实是**限制** ——
#      ⇒ **删掉它，JWT 用户一个能力都没少**。
#   ③ **全仓无消费者**（清点，不是"试了没反应"）—— 无前端 · 测试里只有「**钉它不该被接上限**」
#      的反向守卫（不是"在用"）· Postman 集合里 1 个文件夹。
#
# **它【不欠】隔离账** —— 2026-10-03 乙段已补 `WHERE requested_by`（§1.2 第 7 条）。
#    ⇒ 删它的理由**全部与隔离无关**，别把本段读成隔离收口的一部分（同  §二）。
#
# **别照抄这个形状再把端点加回来** —— `app/tests/test_removed_endpoints.py::test_rag_jwt_ask_stays_removed`
#    会红（判据是 **404**，不是"不是 200"：它删之前带鉴权，回的就是 **401**）。
# 全文（消费者清点 · 改动清单 · 反悔成本）

# ==================== 流式输出（SSE） ====================
# 2026-09-20 删（D1/pyflakes 报 redefinition）：此处的 `StreamingResponse` 与 `json`
#    在文件头早已导入过 ⇒ 删这两行。同段的 `import asyncio` **不是重复**（文件头没有），**必须留**。
# 2026-10-02 删：同段的 `from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_CHAT`
#    已**不再被引用** —— 两个构造点都改走 `make_llm()`，那三个值由工厂统一读。
import asyncio
# 2026-10-04 删（剩余 4 条链 · 批 3）：`anyio` / `track_stream_cancel` / `logger` /
#    `StreamingResponse` 四个 import **已从本文件移除** —— 它们此前**只被** `stream_search`
#    那段内联生成器用到，而那段整块搬进了 `app/routing/sse.py`（`shield=True` 关流 · 计数 · 日志
#    · `StreamingResponse` 三件都在那边，**只是一份**）。留着就是没人用的 import。
# 本仓所有流式端点共用 `app/routing/sse.py` 的那副骨架（含 5 条实测约束的顺序）。
from routing.sse import DONE_FRAME, llm_chunk_text, sse_frame, sse_response, sse_stream

# 2026-09-17 重构 ⑥ 切开点 5：惰性单例。
#    原先此处是【模块层】直接 `llm_stream = ChatOpenAI(...)`
#    `import api_v1_rag`（进而 `import main`）**在导入期就构造 LLM 对象**，
#    哪怕这个进程根本不走流式接口。
#    现改为**首次使用时才建**；此后复用同一实例（与原先的"单例"语义一致）。
_llm_stream = None

def get_llm_stream():
    """惰性构造流式 LLM（首次调用时才建，之后复用）。"""
    global _llm_stream
    if _llm_stream is None:
        # 角色 = 「模型轴 chat」+「长度轴 answer(2000)」—— 见 `app/core/llm_factory.py` 的模块 docstring。
        #    惰性仍在：`make_llm` 自己把 `langchain_openai` 的 import 关在函数内。
        _llm_stream = make_llm(
            "chat", "answer",
            temperature=0.3,
            streaming=True,  # 关键：开启流式模式
        )
    return _llm_stream


class _StreamUsageTap:
    """把「发帧」与「记账」分开：`extract` 逐块累积，`record` 在收尾时落一笔账。

    ## 为什么必须是它，不能靠骨架

    `sse.sse_stream` 只把**通过 `text` 过滤的块**攒进 `collected` ——
    而本仓 provider 把 `usage_metadata` 挂在**最后一帧、且那帧 `content=''`** 上
    （实测 ⇒ `qingyun-rag-agent-TODO待办/探针-流式与记账.py` §结论 1/2）
    ⇒ 它**必然**被 `llm_chunk_text`（`return chunk.content or None`）滤掉。
    ⇒ 累积必须发生在**过滤之前**。骨架里唯一看到**每一块**的钩子就是 `extract`
      （`sse.py`：`text = extract(item) if extract is not None else item` 排在
      `if not text: continue` **之前**）。

    **所以这里的 `extract` 有副作用**（既发帧、又攒账）。这是**有意的**：
       两个动作的输入是**同一串块**；分两处遍历 = 要么重复读流，要么漏掉空 content 那帧。

    **别把 `content` 过滤搬进来** —— 那一帧正是 usage 的载体。
       守卫 ⇒ `app/tests/test_rag_billing_wiring.py::test_stream_search_bills_the_usage_frame_that_has_no_text`
    """

    __slots__ = ("_agg",)

    def __init__(self):
        self._agg = None          # 聚合块（`AIMessageChunk.__add__`）

    def extract(self, chunk):
        """喂给 `sse_stream` 的 `extract`：**累积所有块**，返回该发的文本（沿用旧口径）。"""
        # 对【所有】块做 `+`，不跳空 content（`plan_execute.py:202-205` 同款写法）
        self._agg = chunk if self._agg is None else self._agg + chunk
        return llm_chunk_text(chunk)

    def record(self, llm, *, user_name, thread_id):
        """收尾（**只在这条出口**）时调用：先记账，**成了才返回帧载荷**。

         契约 B：返回的是 `usage` 帧的载荷（`dict`），不再是 `bool`。
           判据只有一条 —— **记账了才出帧、没记账就不出帧**（两者同源）。
           载荷由 `token_tracker.usage_summary` 拼，**不在这里重算一遍模型名与钱**
           （那会造出第二份取数口径， 的代价就是从那儿来的）。

        它**只在正常跑完**那条路被调 —— 取消 / 异常时带 usage 的那一帧根本没到
           ⇒ 如实不记、也不出帧，**不编一个数进账本**（§遗留·2）。
        """
        if self._agg is None:
            return None
        if not record_from_response(
            llm, self._agg, "answer_generation",
            user_name=user_name, thread_id=thread_id,
        ):
            return None
        return usage_summary(llm, self._agg)


# 2026-10-04：`INTERRUPTED_SUFFIX` 与 `_persist_interrupted_turn` **搬去
#    `app/core/cache.py`** —— 6 条流式端点现在共用同一段留痕（`persist_turn`），
#    本文件只**再导出**这个名字（`app/tests/test_cancel_propagation.py` 按 `rag_mod.INTERRUPTED_SUFFIX` 取它）。
#    搬家的理由不是"整理"：只有一份实现， 那三条规则（成对写 / 空答案不写 / 非 done 带尾巴）
#       才不会在 6 处各写一遍、各漏一条。


#  ①：无据拒答的那句话**只说一次**，下面**两处共用** ——
#    ① `citations` 分支的 system prompt（「请**直接说**…」）；
#    ② 收尾时判定「这一轮算不算拒答」。
#    非抽常量不可的理由：这两处**必须一致**，而它们分家的表现是**静默的** ——
#       prompt 里换了措辞 ⇒ 判据认不出来 ⇒ `no_answer` 帧永远不发
#       页面上只是一句普通回答，**没有任何报错**（本仓「两处各写一遍 ⇒ 静默漂移」那一族）。
# 判据是「**以它开头**」，不是"含"：2026-10-06 spike 实测 **8/8** 模型都把它放在**开头**
#    （与 prompt 里那句「请**直接说**」相符）。**误判比漏判有害**（把答得好的那轮画成"资料里没有"）
#    ⇒ 宁可严。代价写实：换过措辞的拒答（如「文档未提及净利润，因此无法回答该问题。」）会被漏掉，
#    那只是**退化成今天的样子**，不会更糟。取舍全文。
REFUSAL_SENTENCE = "根据现有资料，无法回答"


@router.post("/rag/stream_search")
async def stream_search(
    req: QuestionRequest,
    #  裁定 #12：空串挡在**进端点之前**（422）—— 否则它会一路走到
    #    `session_key()` 的 `ValueError`，而那时**流已经开了一半**，只能变成 500。
    thread_id: str = Query("default", min_length=1),   # B8 补：本端点原先**没有** thread_id
    user_name: str = Depends(get_current_user_hybrid),
):
    """流式RAG问答接口（融合优化版）（支持引用溯源和历史补偿）。
    使用SSE逐字返回生成的答案，提供类似ChatGPT的体验。

    2026-09-20 修：这段 docstring 原先**躺在两句代码之后**（函数体第三句），
       是**空操作** —— 函数本身**没有 docstring**。已上移到签名正下方。
    """
    # （2026-10-08 ·  §六·1）：**检索链也要建轨迹**。
    #    改前全仓只有 2 处 `start_trace`、**都在 Agent 链** ⇒ 演示路径（`/chat` 走的就是本端点）
    #    **从不建轨迹** ⇒ 打开 `/trace`，上半页**是空的**（不是坏，是**从没建过**）。
    #
    # **位置：排在 B8 / B11 两道闸【之前】** —— 与 Agent 链**同序**，理由抄
    #    `app/routing/api_v1_agent.py:1646` 那句原话：「超限被拒时，追踪里仍留得下这次尝试的痕迹」。
    #    别"顺手"挪到闸后面 —— 那会让**被拦下的请求**在页面上**消失**，
    #    而"被拦下的请求"恰恰是排查时最想看到的那一类。
    # `user_name` 是端点参数（`Depends(get_current_user_hybrid)`）⇒ 追踪轴也是**按人分**的
    #    （存储键 = `session_key(user_name, thread_id)`，见 `tool_visualizer` 模块头）。
    start_trace(user_name, thread_id, req.question)

    # 0. 若前端未主动传历史，则从 Redis 加载该用户最近5轮对话
    if not req.conversation_history:
        req.conversation_history = get_chat_history(user_name, thread_id=thread_id)

    # B8 · 会话级 token 上限—— 触顶直接拒绝。
    # 放在**取历史之后、检索之前**：这是本端点**第一处真花钱**的位置之前。
    # `scope="session"`（`R3.2`）：两种熔断 `code` 相同 ⇒ 必须显式标出
    #    是哪种，否则前端写不出「何时恢复」（会话级**开个新会话立刻能继续**）。
    ok, why = check_session_token_budget(user_name, thread_id)
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why, scope="session")

    # B11 · 全站日级熔断（`①b`）—— 与上一段并列、都要过。
    # `scope="global"`：全站共享 ⇒ 用户**做什么都救不回来**，只能等跨天。
    #    与上面那句**不是一回事**，别合并。
    ok, why = circuit(global_key())
    if not ok:
        raise AppException(ErrorCode.QUOTA_EXCEEDED, why, scope="global")
    # 1. 检索（与普通接口相同）
    # 1. 向量检索（这部分不是流式的，一次性查完）
    # 构建当前输入的这条的历史对话，真停止按钮的调用（使它支持历史补偿）
    # 🔴：本条此前是**半个** —— 答案生成那笔已经带了调用者的 `thread_id`，
    #    只有 embedding 这一处漏了（落 `"system"`）⇒ 同一个会话的两笔账在两个桶里。
    query_embedding = get_embedding(req.question, user_name=user_name, thread_id=thread_id)
    # 2026-10-03 乙段（§1.2 第 8 条）：改用**共享层**，不再自己写 SQL。
    #    原先这段 SQL **零 `WHERE`** ⇒ 查全库。实测（`app/tests/test_isolation.py`）：
    #    isolation_b 的检索上下文里**逐字**出现了 isolation_a 的文档。
    #    `search_similar` 已带 `WHERE requested_by = %s`，且**返回同样的 4 列**
    #    （id, content, source, similarity）⇒ 下面 `r[0..3]` 的映射不用改。
    #    身份是端点传下去的 —— 这就是「共享层承重」（决策 5）。
    results = search_similar(query_embedding, req.top_k, user_id=user_name)

    # 2. 构建上下文列表（用于可能的引用模式）
    contexts = []
    for r in results:
        contexts.append({
            "id": r[0],
            "content": r[1],
            "source": r[2],
            "similarity": r[3]
        })

    if req.citations:
        # 引用模式：构建带编号的上下文和引用规则 Prompt
        context_parts = []
        sources_list = []
        for i, doc in enumerate(contexts, start=1):
            context_parts.append(f"[文档{i}来源：{doc.get('source', '未知')}]\n{doc['content']}")
            sources_list.append({
                #  契约 A：`index` 与上一行的 `[文档{i}]` **必须来自同一个 `i`**。
                #    别在别处再算一次编号 —— 两处各写一遍 ⇒ 静默错位
                #    （点开的是对的文档、内容是错的那篇），而没有任何报错。
                "index": i,
                "id": doc.get("id"),
                "source": doc.get("source", "未知"),
                #  契约 A：`content` 是**全文**，给"点开引用"看。
                #    `content_preview` 仍在（老前端用它）—— 不是替换，是追加。
                "content": doc["content"],
                "content_preview": doc["content"][:100],
                #：硬门 B 的证真那句要求「点开能看到 **chunk id + 相似度分**」。
                #    这个数**本来就在手上**（上面 `contexts` 里的 `r[3]`）—— 只是没人往帧里放。
                # **两个出口**（本处 + `answer_with_citations.py`）必须保持同形，
                #    有 `test_both_sources_exits_have_the_same_key_set` 钉着。
                "similarity": doc.get("similarity"),
            })
        context_text = "\n\n".join(context_parts)

        system_prompt = f"""你是一个严谨的问答助手。请严格根据以下上下文回答用户的问题。

**引用规则（必须遵守）：**
1. 当你使用上下文中的某条信息时，必须在句末标注来源编号，格式为 `[来源:X]`，其中 X 是文档编号。
2. 如果一句话使用了多个来源，标注为 `[来源:X, Y]`。
3. 不要编造任何上下文以外的信息。如果上下文不足以回答问题，请直接说“{REFUSAL_SENTENCE}”。

**上下文文档：**
{context_text}"""
    else:
        # 普通模式
        context_text = "\n\n".join([doc["content"] for doc in contexts])
        system_prompt = f"""你是一个专业的问答助手。请严格根据以下上下文回答用户的问题。
上下文：
{context_text}"""
        sources_list = []

    # 3. 构建当前消息列表（如果有停止当前消息先放历史，如果没有即为空，再放当前提示词）真停止按钮的调用（使它支持历史补偿）
    messages = []
    if req.conversation_history:
        messages.extend(req.conversation_history)

    messages.extend([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": req.question}
    ])
    
    # 4. 流式生成器
    ENDPOINT = "rag_stream_search"    # Prometheus 的 label，别与函数名混用
    tap = _StreamUsageTap()           #：流式答案的记账（见类 docstring）

    # 2026-10-04（剩余 4 条链 · 批 3）：**内联生成器整个换成 `sse.sse_stream` 骨架**。
    #    这段原来有 ~80 行 `try / except / except / finally`，与 `/agent/langgraph_chat/stream`
    #    **逐字重复**（那 5 条实测约束因此被抄了两遍）。现在只有 `app/routing/sse.py` 一份。
    #    **等价不是"看起来一样"** —— 下面两处**很反直觉的旧契约**是本端点特有的，
    #    **别在"抽公共层"时顺手修正**（前端的适配是照着它们写的）：
    #      ① `[DONE]` 在 `sources` **之前**（见 `_complete`）；
    #      ② 错误路径**没有** `[DONE]`（见 `_on_error`）。
    #    判据：`app/tests/test_cancel_propagation.py` **全绿，且一行断言都没改**。
    async def _complete(collected):
        """收尾尾巴（正常跑完才进）—— **帧序是有讲究的，别整理成"先 sources 再 `[DONE]`"**。

        `[DONE]` 排在 `sources` **之前**不是笔误，是本端点一直以来的线上契约（前端已按此适配）。
           抽公共层**不是**顺手改行为的理由 —— 所以骨架**不替我们补 `[DONE]`**，
           整条尾巴交给本函数自己产（见 `app/routing/sse.py` 的「②」）。
        """
        # 2026-10-06：记账排在【任何 `yield` 之前】（`sse.py` 约束① 同源）——
        #    `yield` 是 await 点，被"二次投递的取消"打断 ⇒ 排在它后面的收尾**一件都不跑**，
        #    可钱**已经花了**。`persist_turn` 仍留在原位（那是  定的，本轮不动它）。
        #  契约 B：`record` 现在**返回载荷**（`None` = 没记成）。
        payload = tap.record(get_llm_stream(), user_name=user_name, thread_id=thread_id)
        # （2026-10-08 ·  §六·1）：**收尾**这条轨迹。
        #    与上面 `tap.record` **同一个位置理由**：排在**任何 `yield` 之前** ——
        #       `yield` 是 await 点，被"二次投递的取消"打断 ⇒ 排在它后面的收尾**一件都不跑**。
        #    `total_tokens` / `total_cost` **不传** —— 与另两个调用点保持一致。
        #       `app/static/js/trace.js` 的 `summarizeTrace` 明写：「服务端这两个字段【现在恒为 0】……
        #       ⇒ 本页**不显示**它们（免得把假数印出来）」⇒ 要传它们是**另一件事**（得连页面一起改）。
        #    取消 / 出错两条出口**不收尾** —— 与 Agent 链同款（「追踪里留一条'开了没结束'」）。
        finish_trace(user_name, thread_id, "".join(collected))
        # 发送结束信号
        yield DONE_FRAME
        #  ①：**这一轮算不算"无据拒答"** —— 让"拒答了"成为一个
        #    **机器可读的字段**，不是让前端去读正文猜。判据与 prompt 那句**共用同一个常量**
        #    （`REFUSAL_SENTENCE`，理由见它的注释）。
        # 位置：`[DONE]` 之后、`sources` **之前** —— 它描述的是**刚结束的那段答案**，
        #    而 `sources` 那一趟前端会重画；标志先到，那一趟才能**一次画对**。
        #    别挪到 `usage` 后面（前端得多收一帧才敢下判断，白多一次重画）。
        # 它**只说"这轮拒答了"**，不把提示文案塞进帧里 —— 文案属于呈现层，
        #    塞进帧就变成又一份要两边同步的契约（`app/static/js/sse.js` 的 `refusalNotice`）。
        if "".join(collected).lstrip().startswith(REFUSAL_SENTENCE):
            yield sse_frame({"no_answer": True}, ensure_ascii=True)
        # ---- 在这里记录对话历史 ----
        # 生成完成后，将本轮问答自动存入 Redis
        #：三条出口（`done` / `cancelled` / `error`）**共用 `persist_turn`**
        #    ⇒ `status` 这个区分只有一份实现，不是在这里手写一遍 `append_chat_history`。
        #    `done` 的字节与改前**逐字相同**（答案原样，不 strip、不带标记）。
        #    **唯一的行为变化**：空答案 ⇒ **连提问也不写**（旧代码会写下孤零零的提问）。
        persist_turn(user_name, req.question, "".join(collected), thread_id=thread_id, status="done")
        # 如果有引用，在结束后发送来源列表
        if req.citations and sources_list:
            yield sse_frame({"sources": sources_list}, ensure_ascii=True)
        #  契约 B：本轮的 token / 费用 —— **纯追加的最后一帧**。
        #    不许挪到 `[DONE]` 之前（那是线上契约，前端按"见 [DONE] 后才收 sources/usage"适配）。
        #    取消 / 出错时**没有**这一帧 —— 前端按"缺这一帧"处理，别补一个 0。
        if payload is not None:
            yield sse_frame({"usage": payload}, ensure_ascii=True)

    async def _on_error(exc, collected):
        """**错误路径的旧行为：只有 error 帧、没有 `[DONE]`** —— 显式覆盖骨架的默认尾巴。

        骨架的默认是"error 帧 + `[DONE]`"（= `/agent/*` 四条链的现状）。本端点**故意不要**它：
           **`[DONE]` 会被读成"正常收尾"**，而本端点今天靠"**没有** `[DONE]`"分辨出错
           ⇒ 加上它会把"出错"变得**像正常结束**。
        """
        # 兜底：其他未知错误
        print(f"流式生成出错: {exc}")
        yield sse_frame({"error": str(exc)}, ensure_ascii=True)

    return sse_response(sse_stream(
        # 上游必须是**异步**的：
        #    同步 `for chunk in stream` 会**阻塞事件循环**，取消得等"下一块到达"才送得进来
        #    ⇒ 上游卡住时，最坏要等一整个 chunk 的时间才停得下来。
        lambda: get_llm_stream().astream(messages),
        endpoint=ENDPOINT,
        # 这是**裸 LLM**（不是图）⇒ 旧口径用 `llm_chunk_text`。空 `content` 由它一并挡掉
        #    （不过滤 ⇒ 前端收到一串空白帧）。
        #：现在传 `tap.extract` —— 它**返回的仍是 `llm_chunk_text(chunk)`**（发帧口径不变），
        #    只是**顺手把每一块 `+` 进聚合**（usage 挂在最后一帧、而那帧 `content=''` ⇒ 见类的 docstring）。
        extract=tap.extract,
        on_complete=_complete,
        #：**取消与异常两条出口**都把**已经生成的那半截**补存进历史（不是直接丢）。
        #    它**必须是同步的**（`persist_turn` 走同步的 `append_chat_history`）——
        #       骨架会在 `await aclose()` **之前**调它，这正是"晚切也存得下"的原因。
        #    骨架的 `logger.info` 与 `_on_error` 里的 `print` 不冲突：前者进日志文件，后者仍在 stdout。
        on_incomplete=lambda collected, status: persist_turn(
            user_name, req.question, "".join(collected), thread_id=thread_id, status=status,
        ),
        on_error=_on_error,
        # **`ensure_ascii=True` 不是可有可无的**：本端点三帧一直用**默认的 `True`**
        #    （中文变 `\uXXXX`），而骨架的默认是 `False`（中文原样）⇒ 不显式传，**线上字节就变了**。
        #    两种编码 **JSON 解码后值相同**（前端走 `JSON.parse`，功能无感）——
        #    但它会让"逐帧等价"从"字面为真"变成"差不多"⇒ 本轮保持原字节，统一与否另案裁。
        ensure_ascii=True,
        # 旧实现在每次 `yield` 后有 `await asyncio.sleep(0.01)` **限速** —— 本轮**不改节奏**，
        #    照旧传下去。别因为"抽公共"就悄悄删掉它。
        chunk_delay=0.01,
    ))

# ==================== WebSocket 端点 ====================
# 模拟: 客户端发送用户问题，服务端模拟 Agent 的思考-行动-观察循环
from fastapi import WebSocket, WebSocketDisconnect
# 2026-09-20 删（D1/pyflakes 报 redefinition）：此处的 `import json` / `import asyncio`
#    在本文件**已被导入过两次**（文件头 + SSE 段）⇒ 删这两行。
from routing.websocket_callback import WebSocketAgentCallback

# 2026-10-08：`from datetime import datetime` **删了** ——
#    它**只**服务于 `get_agent_executor()` 里那个自带的 `date_today`（已删，改为共享工具）。

# 2026-09-17 重构 ⑥ 切开点 5：惰性单例。
#    原先下面这一整段（初始化 LLM / 定义三个工具 / 建 prompt / create_tool_calling_agent /
#    AgentExecutor）**全在模块层** ⇒ `import api_v1_rag`（进而 `import main`）
#    **在导入期就构造 LLM 与 Agent 对象**，哪怕这个进程从不打开 `/ws/agent`。
#    现整段搬进 get_agent_executor()，**首次使用时才建**，之后复用（与原先单例语义一致）。
#    工具 docstring 与 prompt 文本**逐字未改** —— 那是给 LLM 看的接口。
_agent_executor = None
#：与 `_agent_executor` **同一个单例里**的 LLM —— 记账要用它的 `model_name`。
# 别让记账去猜模型名（：本仓因写死 `"qwen-turbo"` 按错的单价记过账）。
_agent_llm = None

def get_agent_executor():
    """惰性构造 WebSocket Agent（首次调用时才建，之后复用）。"""
    global _agent_executor, _agent_llm
    if _agent_executor is None:
        # 全部放在函数内：导入期不拉 langchain
        #
        # 2026-10-09：**这一行原本是 `from langchain.agents import …`** ——
        #    `langchain` 升到 1.x 之后，**那两个符号在那里已经没有了**
        #    （实测 `AttributeError: module 'langchain.agents' has no attribute 'create_tool_calling_agent'`），
        #    本函数**当场 ImportError** ⇒ `/ws/agent` **整条活路径起不来**。
        #    ⇒ 官方把 legacy agent API 搬进了 **`langchain-classic`**（`langchain-core>=1.4.4` 兼容）
        #      ⇒ 改成从那里导入，**实现一行没动**。
        #    **别以为"测试全绿"就是没坏** —— `app/tests/test_ws_auth.py` 那两条用例
        #       `monkeypatch.setattr(api_v1_rag, "get_agent_executor", …)` **把本函数整个换掉了**
        #       ⇒ 它们测的是"鉴权之前端点体不许跑"（对的），**但不构成本函数能用的证据**。
        #    判据（可打印，别用 pytest 当这条的凭证）：
        #       `./venv/bin/python -c "import sys;sys.path.insert(0,'app');import routing.api_v1_rag as m;m.get_agent_executor()"`
        #       ⇒ 不抛 ImportError
        from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
        from langchain_core.prompts import ChatPromptTemplate
        # 2026-10-08 收口：**实现**改为调**共享工具**，**外壳保持 async**。
        #
        #    **为什么不能直接把这三个换成 `mcp_server` 那份**：原文是 `async def`，且
        #       `return await asyncio.to_thread(...)` —— **有意把阻塞丢出事件循环**。
        #       `mcp_server` 那份是**同步**的 ⇒ 直接替换会**在事件循环里同步跑 20 秒的网络调用**。
        #       ⇒ 保留 async 外壳，只把**实现**与**工具描述**指向共享工具（那才是本 task 的目的）。
        #    **【工具 schema 变更】**：工具名由 `search` 改为 `web_search`
        #       （= 注册表里的真名，与 `SENSITIVE_TOOLS` 默认值一致）；`calculator` /
        #       `date_today` 的 **docstring 也换成共享那份**（更详细）。
        #
        #一 初始化模型
        # 角色 = 「模型轴 chat」+「长度轴 answer(2000)」—— 见 `app/core/llm_factory.py` 的模块 docstring。
        llm = make_llm("chat", "answer")
        _agent_llm = llm
        #二 定义工具
        from langchain_core.tools import StructuredTool
        from tools.mcp_server import TOOLS as _MCP_TOOLS

        _src = {t["func"].name: t["func"] for t in _MCP_TOOLS}

        def _async_shell(src):
            """把**同步**工具包成 async 壳：`to_thread` 把它丢出事件循环。

            `name` / `description` / `args_schema` **全部取自共享工具** ——
               这样"给 LLM 的工具描述"也只有一处事实源（本 task 的目的）。
            """
            async def _call(**kwargs):
                return await asyncio.to_thread(src.invoke, kwargs)

            return StructuredTool.from_function(
                coroutine=_call,
                name=src.name,
                description=src.description,
                args_schema=src.args_schema,
            )

        # `eval` 那条没丢：`calculator` 的实现仍在
        #    `app/tools/safe_math.py`，三道闸与守卫在 `app/tests/test_safe_math_wiring.py`。
        #    这里**曾经是本仓第 6 份 `calculator` 拷贝**，且写的是
        #       `asyncio.to_thread(eval, expression)`（`eval` 是**实参**）⇒ 旧判据
        #       （只认「`ast.Call` 的 func 是裸名 `eval`」）**两道守卫都看不见它**，
        #       而**命令真的跑了**（实测返回值 `'0'`）。⇒  已修那两条判据。
        search = _async_shell(_src["web_search"])
        calculator = _async_shell(_src["calculator"])
        date_today = _async_shell(_src["date_today"])

        tools = [calculator, date_today, search]

        # ========== 6. 创建Agent ==========
        # 这是一个专为工具调用设计的标准模板
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "You are a helpful assistant. Use the provided tools to answer the user's question. "
                "If a tool is needed, call it with the appropriate arguments. "
                "After receiving the tool's result, continue reasoning or give the final answer."
            ),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ])
        agent = create_tool_calling_agent(llm, tools, prompt)
        _agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
    return _agent_executor


def get_agent_llm():
    """取与 `get_agent_executor()` **同一个单例**里的 LLM（记账要用它的 `model_name`）。"""
    get_agent_executor()
    return _agent_llm


@router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket, ws_user_name: str = Depends(require_ws_user)):
    # **不要在这里 `await websocket.accept`** —— `require_ws_user` 已经 accept 过了，
    #    而星型 1.6.0 的 `accept()` **不幂等**（第二次会 RuntimeError，实测）。
    #
    # **身份来自上面那个依赖**—— 本条 WS 此前**整条没有鉴权**
    #    （遗留·1）：真花钱（LangChain Agent + chat LLM + `web_search`）
    #    却匿名可达。端点体要**认证通过之后**才会被执行 —— 判据见
    #    `app/tests/test_ws_auth.py::test_ws_agent_body_never_runs_before_auth`。
    #
    # B8 · 会话级 token 上限—— key = `(user_name, thread_id)`。
    # `thread_id` 这里**仍是每连接一个**（`ws-<uuid>`）：WS 没有客户端传上来的会话 id
    #    ⇒ 重连接 = 新桶。**这是已知的、有意留下的口子**，见  遗留·2
    #    （全局日级熔断 B11 仍然罩着，故不因它而失控）。
    ws_session_id = f"ws-{uuid.uuid4().hex}"

    try:
        while True:
            data = await websocket.receive_text()
            request = json.loads(data)
            user_message = request.get("message", "")

            # B8：触顶直接拒绝 —— 用与本文件 `except` 分支**同一套帧格式**（`type` + `content`），
            # 因为 WS 没有 HTTP 状态码可抛（HTTP 端点那边才是 `AppException`）。
            ok, why = check_session_token_budget(ws_user_name, ws_session_id)
            if not ok:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": why
                }))
                await websocket.send_text(json.dumps({"type": "done"}))
                continue

            # B11 · 全站日级熔断（`①b`）—— 与上一段并列、都要过；
            # WS 没有 HTTP 状态码可抛，用**同一套帧格式**（同上面 B8 那里）。
            ok, why = circuit(global_key())
            if not ok:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": why
                }))
                await websocket.send_text(json.dumps({"type": "done"}))
                continue

            # 通知前端开始处理
            await websocket.send_text(json.dumps({
                "type": "thinking",
                "content": f"收到问题：{user_message}，开始分析..."
            }))
            
            try:
                # 使用回调的 ainvoke（create_tool_calling_agent 的输入键是 "input"，输出键是 "output"）
                # 惰性取单例：首次打开 WS 时才构造 Agent
                executor = get_agent_executor()
                # 创建回调实例 —— **必须带身份**：
                #    它现在**同时负责记账**（`on_llm_end`），而账要记到**认证出来的那个人**头上。
                #    三个都是必填 keyword：漏传 = `TypeError`（响亮），不是静默记成 `"unknown"`。
                callback = WebSocketAgentCallback(
                    websocket,
                    user_name=ws_user_name,
                    thread_id=ws_session_id,
                    llm=get_agent_llm(),
                )
                result = await executor.ainvoke(
                    {"input": user_message},
                    config={"callbacks": [callback]}
                )

                # 如果 on_agent_finish 没有被触发，手动发送最终结果
                final_message = result.get("output", "")
                await websocket.send_text(json.dumps({
                    "type": "final",
                    "content": final_message
                }))
                await websocket.send_text(json.dumps({"type": "done"}))
                
            except Exception:
                # 2026-10-10（CodeQL `py/stack-trace-exposure` **同族**）：
                #    **不把异常原文发给客户端** —— 这一帧是**用户可见**的出口，
                #    而 `str(e)` 会把 **SQL 片段 / 连接串 / 文件路径**一起带出去。
                #    **本仓「失败要说出来」那条不变**（不许退化成静默/白屏）——
                #    仍然**说**，只是**不再说内部细节**：细节进服务端日志。
                #    形状照 `api_v1_agent.py` / `token_tracker.py` 那两处（同族先例）。
                # `as e` 已去掉 —— 异常对象交给 `logger.exception` 自己取（`sys.exc_info`），
                #    留着不用反而会被 `ruff` 的  报（本仓的静态检查门会红）。
                logger.exception("WS /ws/agent 执行失败")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": "Agent 执行出错，请稍后重试（详细信息见服务端日志）"
                }))
                await websocket.send_text(json.dumps({"type": "done"}))
    except WebSocketDisconnect:
        print("客户端断开连接")

# 2026-10-05 **删**（§十）：此处原有 **WS `/ws/test`** ——
#    一个纯回声的「WebSocket 基础通信」桩，`ecb146b`（首次提交）起就在。
#    **本墓碑刻意不写那串装饰器字面量**（写成 `WS /ws/test`）——
#       本文件下面  那条墓碑也是这么写的。理由：本仓判据 `grep -c '@router\.' app/routing/api_v1_rag.py`
#       是**数路由**用的，注释里留同款字面串会让它**多数一条**（实测过：带 ⇒ 14，去掉 ⇒ 12）。
#    删的理由（三条，与  删那 4 条**同一套标准**，不是另立一套）：
#      ① **消费者 = 0** —— 本仓没有任何东西连它（`app/static/websocket_test.html` 连的是
#         `/api/v1/ws/agent`）；`git log -S 'ws/test' -- 'app/test_*.py'` 在  之前
#         **零命中**；仓外（任何别的项目）也全 0。
#      ② **它本来就是「测试桩」** ——  的流式出口普查表里就是这么记的。
#      ③ **连「探活」这个唯一可能的用途也没了** ——  给它补上首帧认证之后，
#         它自己也要凭据 ⇒ 留着的唯一理由（免鉴权的 WS 探活口子）已经不成立。
#    这是**删除**，不是「先隐起来」： 那批也是直接删。
#    **别改回来** —— `app/tests/test_removed_endpoints.py::test_ws_test_stays_removed` 会红。

# ==================== 测试 接口 ===================
# ==================== 模拟类 ====================
# 2026-10-03 **删**：此处原有 `POST /rag/ask` —— 同步函数名 `ask_question`。
#    删的理由（三条，都可打印地核过）：
#      ① 它是 `tags=["模拟类测试"]` 的**桩**，却**读真库**（`SELECT content FROM documents …`）；
#      ② 它的 `LIMIT` **没有配套的 `ORDER BY`** ⇒ **结果不可复现**（同一问题两次可能不同）——
#         这正是本仓反复记的那种「看着像检索、其实不是」的形态；
#      ③ 能力被 `/rag/pg_search` **覆盖**（**同鉴权** `get_current_user_hybrid` · **同入参**
#         `QuestionRequest`，且多了 embedding / `ORDER BY` / 更丰富的输出），而全仓**无消费者**。
#    **别照抄这个形状再加回来** —— `app/tests/test_removed_endpoints.py` 会红。
#    顺带消灭了它响应体里那个 **重复的 `"requested_by"` 键**（本仓一处已登记的 dead code）。
#    全文（含消费者清点 · 7 处改动 · 2 处活口径同步 · 反悔成本）

# ============ 【已删除】POST /rag/async_ask · POST /rag/parallel_ask ============
# 2026-10-04 **删除**—— 连同它们**专用的**两个 helper
#    `async_search()` / `parallel_search()` 一并移除（全仓**只有这两条端点**在调它们）。
#
# 原先它们是：
#     async def async_search(query):  await asyncio.sleep(2); return ["异步文档A(…)", …]
#     async def parallel_search(qs):  return await asyncio.gather(*[async_search(q) for q in qs])
#     router.post("/rag/async_ask",    tags=["模拟类测试"])  →  async def async_ask_question(req)
#     router.post("/rag/parallel_ask", tags=["模拟类测试"])  →  async def parallel_ask_question(req)
#     上面两行**故意去掉 `@`** —— 本仓数路由的判据是 `grep -c '@router\.'`，
#        留着 `@` 会让【墓碑注释】也被数进去（判据纪律 #2：注释里也有同样的串）。
#        实测：带 `@` ⇒ 14（错，多算 2）；不带 ⇒ **12**（对）。
#
# 为什么删：
#   **纯 mock** —— 库里没有、embedding 没有、LLM 没有，只是 `asyncio.sleep(2)` 之后
#      返回硬编码字符串 `["异步文档A({query})", …]`。**消费者 = 0**（仓内无前端
#      无测试引用 · 只有 Postman 2 个文件夹，已删）。
#   ⇒ 这两条一删，`tags=["模拟类测试"]` **整组归零**：
#      `/rag/ask` 2026-10-03 删· `/rag/jwt_ask` 2026-10-04 删。
#      **那组四条走完，模式完全一样：先没人用，再删。**
#
# **顺带收掉 `import time`**（文件头第 6 行）：本文件**只有**这两条端点用 `time.time`
#    （全仓已核：`grep -n '\btime\b' app/routing/api_v1_rag.py` 只命中 `import` 与这两处）。
#    `import asyncio` **留着** —— `:794` `:800` 的 `asyncio.to_thread` 还在用。
#
# **别照抄这个形状再把端点加回来** ——
#    `app/tests/test_removed_endpoints.py::test_rag_async_ask_stays_removed` /
#    `::test_rag_parallel_ask_stays_removed` 会红。
# **若真要再加一条「不花钱、不查库」的端点**，必须**同时**做一件事：
#    **重建一份反向接线守卫**（原先是 `test_breaker_wiring.py` 与
#    `test_session_budget_wiring.py` 里那两张豁免清单）—— 那两张清单**随本次删除已空，
#    空清单 = `for` 体一次都不跑 = 恒绿假通过 ⇒ 两段守卫**已删**。详。
# ==============================================================================
