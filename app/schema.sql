-- ============================================================================
-- fastapi-rag-agent · 数据库 Schema
-- ============================================================================
--
-- ## ⛔ 本文件是【生成的】，不要手改
--
-- 生成方式（**从活着的数据库导**，不是手写）：
--
--     # ⇒ 重写本文件
--     --check  # ⇒ 只报「与活库是否一致」，⛔ 不写
--
-- 🔴 **2026-10-08 起【必须走那个脚本】**——
--    ⚠️ **本行原文是这条命令，它【会把手写头冲掉】**：
--        `docker compose exec -T postgres pg_dump … --schema-only … > app/schema.sql`
--    头是手写的、`pg_dump` 不产它 ⇒ 照原文跑一次，**上面这 70 多行当场没了，而且不报错**。
--     那次是**手工 `cat` 拼的**；脚本把这一步变成了结构（**本文件按【哨兵行】切**）。
--
-- 生成时间：2026-09-29 → **2026-10-08 重新生成**→ **2026-10-09 再生成**（B1：`api_keys.role`）
--           → 🔴 **2026-10-14 再生成**（**B 段**：`documents.embedding` 由 `vector(1536)` 改成
--             **`vector(1024)`** —— 换 `qwen3.7-text-embedding-flash` 后**实测**的维度）
--           → 🔴 **2026-10-16 再生成**（：`documents_embedding_idx` 由 `ivfflat`
--             改成 **`hnsw`** —— ⚠️ **顺带补上了 `login_users`**，它 2026-10-16 加进活库后一直没进快照）
-- 来源数据库：本机开发库（Docker 容器 `postgres-rag`，镜像 `pgvector/pgvector:pg17`）
--
-- ## 🔴 2026-10-08 这次重新生成，改了什么
--
-- * ✅ **补上了 `approval_events`**（表 + 序列 + PK + 默认值）——
--   它是**惰性建的**（`app/agent/approval_audit.py` 的 `_DDL` 在写入路径里），
--   所以要它进快照，**前提是那个库上真的发生过一次 approve**。
--   📌 **主判据（⛔ 不会把自己数进去 —— 靠【行首锚】）**：
--      `grep -c '^CREATE TABLE public\.approval_events' app/schema.sql` ⇒ **1**（**改前 0**）。
--   🔴 **这条判据被【同一个人】写歪了两次，两次都是"尺子自我指涉"，所以留下过程：**
--      · 第一版 = `grep -c 'approval_events'` ⇒ 只算 dump 正文是 **11**，
--        但我把这段说明写进去之后，**它连说明一起数** ⇒ 全文件变 **13**。
--      · 第二版 = `grep -c 'CREATE TABLE public.approval_events'` ⇒ 我**又**把这串原样写进说明
--        ⇒ 实测 **2**（1 真 + 1 是这句说明自己）。
--      · ✅ **第三版（现在这条）加 `^` 行首锚** —— dump 是**行首**输出的，而说明里那串在**行中**
--        ⇒ 量到 **1**，且**再怎么写说明都不会动这个数**。
--   ⚠️ **本仓同族**：本仓的复盘记录（⛔ 不随产品仓提供） · 本仓的复盘记录（⛔ 不随产品仓提供）。
--   ⚠️ ** 原写「应为 1」—— 那个数是猜的**（写它的人没见过真 dump）⇒ **真实判据是「0 → 非 0」**，
--      且**要选一个不会被自己的说明撼动的串**。
-- * ⚠️ **抹掉了一处【手改】**：旧文件 `api_keys.is_active` 那行尾上挂着一条
--   `--  按 COALESCE(is_active,1)=1 过滤；写侧是整数 0/1`。
--   🔴 **这正是本文件头第 1 条警告说的那件事**（手改生成物 ⇒ 下次一跑生成命令就静默抹掉）。
--   ⇒ **没有把它加回来**（加回来 = 留给下一次同一个坑）；那条说明的家在
--   `app/core/db.py:80-90` · `docs/契约/数据模型.md` §`api_keys` · 本仓的决策记录（⛔ 不随产品仓提供）。
-- * ℹ️ `\restrict` / `\unrestrict` 后面那串是 pg_dump 每次随机生成的 → **每次导都会变**，非缺陷。
--
-- ## 为什么要有它
--
-- 在此之前，**本仓的表结构【只存在于代码里】** ——
-- `app/core/db.py:56-174` 的 `create_table()` 用 `CREATE TABLE IF NOT EXISTS` 在应用启动时建表，
-- 而 **Alembic 只覆盖其中一部分**。⇒ 新人/新环境**没有一个地方能一眼看到全貌**。
-- 决策见 本仓的决策记录（⛔ 不随产品仓提供）（遗留 #3）。
--
-- ## ⚠️ 三条要紧的
--
-- 1. **它可能与代码不一致** —— 因为 `create_table()` 用的是 `IF NOT EXISTS`，
--    **表已存在时它不会改任何东西**。所以"代码写的"与"库里实际有的"**可能不同步**。
--    📌 **本文件反映的是【库里实际有的】—— 那是更权威的那一份。**
--
-- 2. 🔴 **索引类型与代码"静默不一致"这件事，现在【从结构上消掉了】**（2026-10-16 ·）——
--    `create_table()` 建完会**验 access method**，与 `app/core/db.py::_VECTOR_INDEX_AM` 不符就
--    `DROP` + 重建。⇒ 代码与活库**不可能再各走各的**。
--    ⚠️ **这一处曾经正是最典型的那个例子**（代码里写 `ivfflat`、活库是手工建的 `hnsw`，
--    两边漂移了很久**而没有任何东西盯着**）—— 根因就是那句 `IF NOT EXISTS` **改不动已存在的索引**。
--    📌 **判据（⛔ 不要读这段文字，跑它）**：
--       `SELECT am.amname FROM pg_class c JOIN pg_am am ON am.oid=c.relam WHERE c.relname='documents_embedding_idx'`
--       ⇒ 必须等于 `app/core/db.py::_VECTOR_INDEX_AM`（现为 `hnsw`）。
--    详见 `docs/契约/数据模型.md` §六。✅ **已裁并实施**（不再是 ⬜ 待裁）。
--
-- 3. **重建时注意**：`pg_dump` 导出的 SQL **不含数据**（`--schema-only`）。
--    恢复数据用 `pg_dump`（不带 `--schema-only`）或卷备份，见 `docs/说明/运维.md` §六。
--
-- ## 相关
--
-- | 文档 | 说明 |
-- |---|---|
-- | `docs/契约/数据模型.md` | **逐字段说明**（人读的那份）—— ⚠️ **2026-10-16 起活库是 8 张表，那份还只写到 7 张**（`login_users` 未收录） |
-- | `app/core/db.py` | 建表代码（`create_table()`） |
-- | `app/alembic/versions/` | 迁移（**只覆盖 3 处，不全**） |
-- | `docs/说明/运维.md` §六 | 备份与恢复 |
--
-- ============================================================================
--
-- 🔴 切分规则：本行【以上】是【手写头】，本行【以下】是 `pg_dump` 的【原样输出】。
--     **按下面那行哨兵切**（2026-10-08 起）。
--    ⛔ 别删/别改哨兵行，也别把它挪到别处 —— 改不了就改脚本，⛔ 不是改这里。
-- ============================================================================
-- ⛔⛔ 切分哨兵（**本行是手写头的最后一行** —— ⛔ 后面不许再有手写内容）—— 以下全部是 `pg_dump` 原样输出


--
-- PostgreSQL database dump
--

\restrict mdiXzVdVi7eGgL1BzfkylZFjMjllYeVZK8fY31bLP6ZlbnAfX1Uu5DCxA3pijaf

-- Dumped from database version 17.10 (Debian 17.10-1.pgdg12+1)
-- Dumped by pg_dump version 17.10 (Debian 17.10-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    id integer NOT NULL,
    user_name text NOT NULL,
    key_hash text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp without time zone NOT NULL,
    is_active integer DEFAULT 1,
    role text
);


--
-- Name: api_keys_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.api_keys_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: api_keys_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.api_keys_id_seq OWNED BY public.api_keys.id;


--
-- Name: approval_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.approval_events (
    id integer NOT NULL,
    owner text NOT NULL,
    actor text NOT NULL,
    raw_thread_id text,
    graph text,
    decision text NOT NULL,
    edited boolean NOT NULL,
    rounds integer,
    reason text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: approval_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.approval_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: approval_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.approval_events_id_seq OWNED BY public.approval_events.id;


--
-- Name: budget_intercepts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.budget_intercepts (
    id integer NOT NULL,
    user_name text NOT NULL,
    tool_name text,
    reason text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: budget_intercepts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.budget_intercepts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: budget_intercepts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.budget_intercepts_id_seq OWNED BY public.budget_intercepts.id;


--
-- Name: cost_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cost_records (
    id integer NOT NULL,
    user_name text NOT NULL,
    thread_id text DEFAULT 'unknown'::text,
    model text NOT NULL,
    purpose text NOT NULL,
    prompt_tokens integer DEFAULT 0 NOT NULL,
    completion_tokens integer DEFAULT 0 NOT NULL,
    total_tokens integer DEFAULT 0 NOT NULL,
    input_cost real DEFAULT 0.0 NOT NULL,
    output_cost real DEFAULT 0.0 NOT NULL,
    total_cost real DEFAULT 0.0 NOT NULL,
    tool_name text,
    tool_args text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: cost_records_archive; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cost_records_archive (
    id integer NOT NULL,
    user_name text NOT NULL,
    thread_id text DEFAULT 'unknown'::text,
    model text NOT NULL,
    purpose text NOT NULL,
    prompt_tokens integer DEFAULT 0 NOT NULL,
    completion_tokens integer DEFAULT 0 NOT NULL,
    total_tokens integer DEFAULT 0 NOT NULL,
    input_cost real DEFAULT 0.0 NOT NULL,
    output_cost real DEFAULT 0.0 NOT NULL,
    total_cost real DEFAULT 0.0 NOT NULL,
    tool_name text,
    tool_args text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: cost_records_archive_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cost_records_archive_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cost_records_archive_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cost_records_archive_id_seq OWNED BY public.cost_records_archive.id;


--
-- Name: cost_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cost_records_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cost_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cost_records_id_seq OWNED BY public.cost_records.id;


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id integer NOT NULL,
    content text NOT NULL,
    source text,
    requested_by text DEFAULT 'anonymous'::text NOT NULL,
    embedding public.vector(1024)
);


--
-- Name: documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.documents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.documents_id_seq OWNED BY public.documents.id;


--
-- Name: login_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.login_users (
    user_name text NOT NULL,
    password_hash text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    expires_at timestamp without time zone
);


--
-- Name: token_usage_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.token_usage_logs (
    id integer NOT NULL,
    user_name text NOT NULL,
    thread_id text DEFAULT 'unknown'::text,
    model text NOT NULL,
    purpose text NOT NULL,
    prompt_tokens integer NOT NULL,
    completion_tokens integer NOT NULL,
    total_tokens integer NOT NULL,
    cost real DEFAULT 0.0 NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: token_usage_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.token_usage_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: token_usage_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.token_usage_logs_id_seq OWNED BY public.token_usage_logs.id;


--
-- Name: api_keys id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys ALTER COLUMN id SET DEFAULT nextval('public.api_keys_id_seq'::regclass);


--
-- Name: approval_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approval_events ALTER COLUMN id SET DEFAULT nextval('public.approval_events_id_seq'::regclass);


--
-- Name: budget_intercepts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.budget_intercepts ALTER COLUMN id SET DEFAULT nextval('public.budget_intercepts_id_seq'::regclass);


--
-- Name: cost_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_records ALTER COLUMN id SET DEFAULT nextval('public.cost_records_id_seq'::regclass);


--
-- Name: cost_records_archive id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_records_archive ALTER COLUMN id SET DEFAULT nextval('public.cost_records_archive_id_seq'::regclass);


--
-- Name: documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents ALTER COLUMN id SET DEFAULT nextval('public.documents_id_seq'::regclass);


--
-- Name: token_usage_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.token_usage_logs ALTER COLUMN id SET DEFAULT nextval('public.token_usage_logs_id_seq'::regclass);


--
-- Name: api_keys api_keys_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_key_hash_key UNIQUE (key_hash);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: approval_events approval_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approval_events
    ADD CONSTRAINT approval_events_pkey PRIMARY KEY (id);


--
-- Name: budget_intercepts budget_intercepts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.budget_intercepts
    ADD CONSTRAINT budget_intercepts_pkey PRIMARY KEY (id);


--
-- Name: cost_records_archive cost_records_archive_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_records_archive
    ADD CONSTRAINT cost_records_archive_pkey PRIMARY KEY (id);


--
-- Name: cost_records cost_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_records
    ADD CONSTRAINT cost_records_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: login_users login_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_users
    ADD CONSTRAINT login_users_pkey PRIMARY KEY (user_name);


--
-- Name: token_usage_logs token_usage_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.token_usage_logs
    ADD CONSTRAINT token_usage_logs_pkey PRIMARY KEY (id);


--
-- Name: api_keys_user_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX api_keys_user_name_idx ON public.api_keys USING btree (user_name);


--
-- Name: documents_embedding_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX documents_embedding_idx ON public.documents USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: documents_requested_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX documents_requested_by_idx ON public.documents USING btree (requested_by);


--
-- Name: idx_cost_records_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cost_records_date ON public.cost_records USING btree (created_at);


--
-- Name: idx_cost_records_purpose; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cost_records_purpose ON public.cost_records USING btree (purpose);


--
-- Name: idx_cost_records_thread; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cost_records_thread ON public.cost_records USING btree (thread_id);


--
-- Name: idx_cost_records_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cost_records_user ON public.cost_records USING btree (user_name);


--
-- Name: idx_token_usage_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_token_usage_date ON public.token_usage_logs USING btree (created_at);


--
-- Name: idx_token_usage_thread; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_token_usage_thread ON public.token_usage_logs USING btree (thread_id);


--
-- Name: idx_token_usage_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_token_usage_user ON public.token_usage_logs USING btree (user_name);


--
-- PostgreSQL database dump complete
--

\unrestrict mdiXzVdVi7eGgL1BzfkylZFjMjllYeVZK8fY31bLP6ZlbnAfX1Uu5DCxA3pijaf

