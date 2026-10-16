"""embedding 维度自检的【接线守卫】（`①b`  · 第 4 条）· **纯离线，进 CI**。
"""
import ast
import pathlib

_APP = pathlib.Path(__file__).resolve().parents[1]      # app/
_MAIN = _APP / "main.py"


# ==================== 工具（AST，不是 grep） ====================

def _find_fn(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{_MAIN.name} 里找不到函数 {name}()")


def _called_names(node: ast.AST) -> set:
    """子树里**被调用到的名字**（`Name` / `Attribute` 两种写法都收）。"""
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _dimension_try(tree: ast.AST) -> ast.Try:
    """`startup_event` 里**调了 `embedding_dim_in_db`** 的那个 `try` 节点。

    ⚠️ 用"调用了哪个函数"定位，⛔ **不用行号** —— 行号必然过期。
    """
    fn = _find_fn(tree, "startup_event")
    for n in ast.walk(fn):
        if isinstance(n, ast.Try) and "embedding_dim_in_db" in _called_names(n):
            return n
    raise AssertionError("startup_event 里找不到「调了 embedding_dim_in_db」的 try 块")


def _handler_catches(node: ast.Try) -> list:
    """每个 handler 捕获的异常类型，写成字符串（裸 `except:` ⇒ `['<bare>']`）。"""
    out = []
    for h in node.handlers:
        if h.type is None:
            out.append("<bare>")
        else:
            out.append(ast.unparse(h.type))
    return out


# ==================== ① · 接线在不在 ====================

def test_startup_event_calls_the_selfcheck():
    """启动时真的调了自检 —— 否则那两行代码等于没接（本仓：「看到函数没被调用」那一族）。"""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    try_node = _dimension_try(tree)
    called = _called_names(try_node)
    assert "embedding_dim_in_db" in called, "自检没读表定义"
    assert "assert_model_dimension" in called, "读了表定义却没拿它去比模型维度"


def test_expected_comes_from_the_table_not_a_literal():
    """`expected` 必须来自**表自己** —— ⛔ 不许在 main.py 里再写死一个维度。

    本仓前科：A 段就是因为"硬接会在 main.py 里写第三处 1536"才把接线推到 B 段。
    ⇒ 判据：那个 `try` 块里**不许出现**裸的数字字面量维度。
    """
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    try_node = _dimension_try(tree)
    literals = {n.value for n in ast.walk(try_node)
                if isinstance(n, ast.Constant) and isinstance(n.value, int)
                and not isinstance(n.value, bool)}
    assert literals == set(), f"那段里出现了写死的整数 {sorted(literals)} —— 维度不许硬编码"


# ==================== ② · 两类失败的处置【没有】被合并 ====================

def test_handler_does_not_catch_the_os_error_family_outright():
    """🔴 核心一条：⛔ 不许把 `EnvironmentError` / `OSError` **当成捕那一类**。

    理由：`EnvironmentError` 在 py3 里**就是** `OSError`，而 `ConnectionError` 也是它的子类
    ⇒ 那样写会把**一次网络抖动**当成"维度对不上"。

    ⚠️ **`except Exception` 本身不违规** —— 本仓现在就是那么写的：
    **先全捕、再用 `isinstance` 判别**，那是对的做法。
    违规的是「**捕了 OSError 那一族、然后不带判别地 re-raise**」。
    ⇒ 所以本条只禁**具名捕 `EnvironmentError`/`OSError`**，以及**裸 `except:`**。
    """
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    caught = _handler_catches(_dimension_try(tree))
    bad = [c for c in caught if c == "<bare>" or c in {"EnvironmentError", "OSError"}]
    assert not bad, (
        f"那段 selfcheck 把 {bad} 当成了异常类型 —— "
        "它会把网络失败和维度不匹配混成一类（两类处置相反，见本文件头）"
    )


def test_every_raise_in_the_handler_is_gated_by_the_isinstance_check():
    """维度不匹配必须**往上抛**，且**只有它**往上抛。

    判据（AST）：handler 里**每一个 `raise`** 都必须嵌在一个
    「提到了 `EmbeddingDimensionMismatch`」的 `If` 里。

    ⛔ 这条挡的是那个**看着更干净**的写法 —— `except Exception: raise`、
    或者 `if not isinstance(e, EnvironmentError): log(...)` 那样的取反表达
    （后者会让两类失败**恰好反过来**：网络错抛、维度错只记 warning）。
    """
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    handlers = _dimension_try(tree).handlers
    assert handlers, "那段 try 没有 handler —— 连 warning 都不会有"

    gated, ungated, negated = 0, [], []
    for h in handlers:
        for n in ast.walk(h):
            if not isinstance(n, ast.If):
                continue
            for inner in ast.walk(n):
                if not isinstance(inner, ast.Raise):
                    continue
                if _mentions_mismatch(n):
                    gated += 1
                    # 🔴 光"提到了那个类名"不够 —— `not isinstance(...)` 把两类**对调**，
                    #    而它在 AST 上只是多了一个 `Not`，⛔ 光看名字是看不出来的。
                    #    （变异实测：不加这一条，M2 那个取反**照样全绿**。）
                    if any(isinstance(x, ast.Not) for x in ast.walk(n.test)):
                        negated.append(inner.lineno)
                else:
                    ungated.append(inner.lineno)
    assert gated, (
        "handler 里找不到「命中 EmbeddingDimensionMismatch 就 raise」那一支 —— "
        "那样维度配错会被降级成一条 warning（§2.1 要的正是『响亮地断』）"
    )
    assert not ungated, (
        f"第 {ungated} 行的 raise 不在那条 isinstance 判断里 —— "
        "它会把别的异常也一起抛上去（网络不可用时启动会被挡住）"
    )
    assert not negated, (
        f"第 {negated} 行的判断带 `not` —— 两类失败的处置【对调】了："
        "变成『维度错只记 warning、网络错反而抛上去』，与  §2.1 正好相反"
    )


def _mentions_mismatch(node: ast.AST) -> bool:
    """子树里提到了 `EmbeddingDimensionMismatch`（名字用法或 `isinstance` 的第二个参数两种写法都收）。"""
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == "EmbeddingDimensionMismatch":
            return True
        if isinstance(n, ast.Attribute) and n.attr == "EmbeddingDimensionMismatch":
            return True
    return False


# ==================== ③ · 那两个异常类【确实】能分开（跑出来的） ====================

def test_mismatch_is_distinguishable_from_a_plain_os_error():
    """⚠️ 这一条是本文件的**地基**：如果两者分不开，上面那条 `isinstance` 就是摆设。

    实测：`isinstance(EmbeddingDimensionMismatch(...), EnvironmentError)` ⇒ **True**
    ⇒ 这正是不许写 `except EnvironmentError` 的原因；
    而**反过来**要成立：一个普通的 `ConnectionError` **不是** `EmbeddingDimensionMismatch`。
    """
    from rag.embedding_client import EmbeddingDimensionMismatch

    mismatch = EmbeddingDimensionMismatch("维度对不上")
    assert isinstance(mismatch, EnvironmentError), (
        "前提变了：它不再是 OSError 族 —— 本文件头那段理由要重写"
    )
    plain = ConnectionError("connection refused")
    assert not isinstance(plain, EmbeddingDimensionMismatch), (
        "分不开了 —— 那条 isinstance 判据失效，网络失败会被当成维度错、挡住启动"
    )
