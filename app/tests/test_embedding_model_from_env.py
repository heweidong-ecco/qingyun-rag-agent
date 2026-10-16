"""embedding 模型名必须**从 env 读** —— 不许再写死在签名里（§二·2）。
"""


def _stub_io(monkeypatch, ec, seen):
    """把网络与副作用全打桩，只留「传给 SDK 的 model 是什么」。"""

    class _FakeEmbeddings:
        def create(self, input, model):
            seen["model"] = model

            class _R:
                data = [type("D", (), {"embedding": [0.0] * 4})()]
                usage = type("U", (), {"prompt_tokens": 3})()

            return _R()

    monkeypatch.setattr(ec, "_get_client", lambda: type("C", (), {"embeddings": _FakeEmbeddings()})())
    # 一律用 `*a, **k`： 会给这两个函数**加 `model` 形参**，
    #    写死 `lambda t:` / `lambda t, e:` 会在那一步当场 TypeError（不是业务错，是桩写窄了）。
    monkeypatch.setattr(ec, "get_cached_embedding", lambda *a, **k: None)
    monkeypatch.setattr(ec, "set_cached_embedding", lambda *a, **k: None)
    monkeypatch.setattr(ec, "record_usage", lambda **k: None)


def test_default_comes_from_config(monkeypatch):
    """不传 `model` ⇒ 走 `config.EMBEDDING_MODEL_MAIN`（不是字面量）。"""
    import core.config as config
    import rag.embedding_client as ec

    monkeypatch.setattr(config, "EMBEDDING_MODEL_MAIN", "sentinel-model-name")
    monkeypatch.delenv("EMBEDDING_MODEL_MAIN", raising=False)
    seen = {}
    _stub_io(monkeypatch, ec, seen)

    ec.get_embedding("你好")
    assert seen["model"] == "sentinel-model-name"


def test_explicit_model_still_wins(monkeypatch):
    """显式传 `model=` ⇒ **听它的**（`test_isolation` 那 4 处靠这个形状活着）。"""
    import rag.embedding_client as ec

    seen = {}
    _stub_io(monkeypatch, ec, seen)

    ec.get_embedding("你好", model="explicit-model")
    assert seen["model"] == "explicit-model"


def test_cache_key_separates_models():
    """换模型必须换缓存键 —— 否则会命中**旧模型**的向量（静默错配）。

    改前键是 `"emb:" + md5(text)`，**模型名不在里面** ⇒ 切完模型后同一句话拿回旧向量，
    而它**不报任何错**（维度不同时 pgvector 会报；**维度恰好相同**时连报都不报）。
    """
    import core.cache as cache

    assert cache.get_cache_key("同一句话", "model-a") != cache.get_cache_key("同一句话", "model-b")
    assert cache.get_cache_key("同一句话", "model-a") == cache.get_cache_key("同一句话", "model-a")
    assert cache.get_cache_key("另一句话", "model-a") != cache.get_cache_key("同一句话", "model-a")


# ====================  · 启动自检 ====================

def test_dimension_mismatch_raises(monkeypatch):
    """维度对不上 ⇒ **响亮地断**（不是 warning、不是回落）。"""
    import pytest

    import rag.embedding_client as ec

    monkeypatch.setattr(ec, "get_embedding", lambda text, model=None, **k: [0.0] * 8)
    with pytest.raises(EnvironmentError, match="维度"):
        ec.assert_model_dimension(1024)


def test_dimension_match_passes(monkeypatch):
    """对得上 ⇒ 安静放行（不许变成一个总在响的铃）。"""
    import rag.embedding_client as ec

    monkeypatch.setattr(ec, "get_embedding", lambda text, model=None, **k: [0.0] * 8)
    assert ec.assert_model_dimension(8) is None
