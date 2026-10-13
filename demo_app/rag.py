"""RAG 检索链路（被测系统侧）：纯标准库 BM25 检索 + 可选 embedding 后端。

目的：让「RAG 检索链路测试」有离线、确定性、可回归的被测对象——
语料 + 倒排索引 + 检索 + 语料增删改，整条链路都能被 Agent 断言。

**不引入任何第三方依赖**：中文用「字符 unigram + 相邻 bigram」建索引（中文 IR 的经典做法，
无需 jieba）；BM25 打分只用 math.log。

缺陷开关（默认开启以复现缺陷；全部置 False 即正确实现，见 CORRECT_BUGS）：
  BUG_RAG_CJK_TOKENIZER   中文分词缺失：整段连续中文只切一个 token → 中文查询召回为 0
  BUG_RAG_SORT_ASC        排序方向写反：score 升序返回（契约要求降序）
  BUG_RAG_NO_TIEBREAK     分数并列时未按 doc_id 升序稳定排序 → 并列次序不可预期
  BUG_RAG_DUPLICATE_HITS  未按 doc_id 聚合 → 同一文档在 hits 中重复出现
  BUG_RAG_STALE_INDEX     删除/更新文档后未同步倒排索引 → 陈旧召回（ghost hit）
  BUG_RAG_LOOSE_CONTRACT  空 q / 超长 q / 非法 top_k 未按契约返回 400

契约以 demo_app/openapi.json 为准：实现与契约的偏差就是 Agent 要发现的缺陷。
"""
from __future__ import annotations

import math
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------- 契约常量 ----------

K1 = 1.5
B = 0.75

RAG_DEFAULT_TOP_K = 5
RAG_MAX_TOP_K = 50
RAG_MAX_QUERY_LEN = 200

# ---------- 缺陷开关 ----------

BUG_RAG_CJK_TOKENIZER = True
BUG_RAG_SORT_ASC = True
BUG_RAG_NO_TIEBREAK = True
BUG_RAG_DUPLICATE_HITS = True
BUG_RAG_STALE_INDEX = True
BUG_RAG_LOOSE_CONTRACT = True

RAG_BUGS: Dict[str, bool] = {
    "cjk_tokenizer": BUG_RAG_CJK_TOKENIZER,
    "sort_asc": BUG_RAG_SORT_ASC,
    "no_tiebreak": BUG_RAG_NO_TIEBREAK,
    "duplicate_hits": BUG_RAG_DUPLICATE_HITS,
    "stale_index": BUG_RAG_STALE_INDEX,
    "loose_contract": BUG_RAG_LOOSE_CONTRACT,
}
CORRECT_BUGS: Dict[str, bool] = {key: False for key in RAG_BUGS}

DOC_TIE_A = "DOC-TIE-001"
DOC_TIE_B = "DOC-TIE-002"
_TIE_TEXT = "duplicate text for tie break verification"

# 语料：包含中文文档（检索链路最容易出问题的地方）与一组精确并列的英文文档。
# 注意 DOC-TIE-002 故意排在 DOC-TIE-001 之前，用于暴露「并列时未按 doc_id 排序」的缺陷。
DEFAULT_CORPUS: List[Tuple[str, str]] = [
    ("DOC-CN-001", "项目使用 Python 实现检索服务，包含接口层与测试层。"),
    ("DOC-CN-002", "向量数据库支持语义检索与相似度计算。"),
    ("DOC-CN-003", "BM25 算法基于词频与逆文档频率计算相关性得分。"),
    ("DOC-CN-004", "中文分词质量直接影响检索召回率与准确率。"),
    ("DOC-CN-005", "索引重建可以修复数据与索引不一致的问题。"),
    ("DOC-EN-001", "vector database supports semantic search and similarity"),
    ("DOC-EN-002", "bm25 ranking algorithm uses term frequency and inverse document frequency"),
    ("DOC-EN-003", "index rebuild fixes inconsistency between data and index"),
    ("DOC-EN-004", "search quality metrics include recall and mean reciprocal rank"),
    (DOC_TIE_B, _TIE_TEXT),
    (DOC_TIE_A, _TIE_TEXT),
]

_TOKEN_RUN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")


def tokenize(text: str, buggy_cjk: bool = False) -> List[str]:
    """分词：ASCII 词小写化；中文切「字符 unigram + 相邻 bigram」。

    buggy_cjk=True 复现「中文分词缺失」缺陷：整段连续中文只切出一个 token，
    于是中文查询与中文文档之间不可能有 token 交集，召回直接归零。
    """
    tokens: List[str] = []
    for run in _TOKEN_RUN.findall(text or ""):
        low = run.lower()
        if low.isascii():
            tokens.append(low)
            continue
        if buggy_cjk:
            tokens.append(run)
            continue
        tokens.extend(low)
        if len(low) > 1:
            tokens.extend(low[i:i + 2] for i in range(len(low) - 1))
    return tokens


class EmbeddingUnavailable(RuntimeError):
    """embedding 后端不可用（未配置 Key / 网络异常 / 接口返回异常）。"""


class BM25Backend:
    """BM25 检索后端，纯标准库实现。"""

    name = "bm25"

    def __init__(self, buggy_cjk: bool = False) -> None:
        self.buggy_cjk = buggy_cjk
        self._postings: Dict[str, Dict[str, int]] = {}
        self._doc_len: Dict[str, int] = {}
        self._avgdl = 0.0

    # ----- 索引维护 -----

    def build(self, docs: Dict[str, str]) -> None:
        self._postings = {}
        self._doc_len = {}
        for doc_id, text in docs.items():
            self._index_one(doc_id, text)
        self._refresh_avgdl()

    def add(self, doc_id: str, text: str) -> None:
        self._index_one(doc_id, text)
        self._refresh_avgdl()

    def remove(self, doc_id: str) -> None:
        for term, bucket in list(self._postings.items()):
            bucket.pop(doc_id, None)
            if not bucket:
                del self._postings[term]
        self._doc_len.pop(doc_id, None)
        self._refresh_avgdl()

    def _index_one(self, doc_id: str, text: str) -> None:
        tokens = tokenize(text, self.buggy_cjk)
        self._doc_len[doc_id] = max(len(tokens), 1)
        for term in tokens:
            bucket = self._postings.setdefault(term, {})
            bucket[doc_id] = bucket.get(doc_id, 0) + 1

    def _refresh_avgdl(self) -> None:
        if self._doc_len:
            self._avgdl = sum(self._doc_len.values()) / len(self._doc_len)
        else:
            self._avgdl = 0.0

    def indexed_ids(self) -> List[str]:
        return list(self._doc_len)

    # ----- 检索 -----

    def score_contributions(self, query: str) -> List[Tuple[str, float]]:
        """返回 (doc_id, 分数贡献) 列表：每个 query term 命中一次就产出一条。

        聚合与去重由 RagService._rank 负责——这正是缺陷 BUG_RAG_DUPLICATE_HITS 的注入点。
        """
        terms = tokenize(query, self.buggy_cjk)
        total = max(len(self._doc_len), 1)
        avgdl = self._avgdl or 1.0
        out: List[Tuple[str, float]] = []
        for term in terms:
            bucket = self._postings.get(term)
            if not bucket:
                continue
            df = len(bucket)
            idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            for doc_id, tf in bucket.items():
                dl = self._doc_len.get(doc_id, 1)
                denom = tf + K1 * (1 - B + B * dl / avgdl)
                out.append((doc_id, idf * tf * (K1 + 1) / denom))
        return out


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class EmbeddingBackend:
    """可选 embedding 检索后端：调用 OpenAI 兼容的 /embeddings 接口。

    - 只用标准库 urllib，且 import 延迟到真正发请求时；不构成任何硬依赖；
    - 未配置 Key / Base URL、网络不可达、接口结构异常时一律抛 EmbeddingUnavailable，
      由 RagService 优雅降级回 BM25 —— 离线 CI 与默认演示完全不受影响。
    """

    name = "embedding"

    ENV_KEY = "AGENTTEST_EMBEDDING_API_KEY"
    ENV_BASE = "AGENTTEST_EMBEDDING_BASE_URL"
    ENV_MODEL = "AGENTTEST_EMBEDDING_MODEL"
    DEFAULT_MODEL = "text-embedding-3-small"

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "", timeout: float = 10.0) -> None:
        self.api_key = api_key or os.environ.get(self.ENV_KEY, "") or os.environ.get("AGENTTEST_LLM_API_KEY", "")
        base = base_url or os.environ.get(self.ENV_BASE, "") or os.environ.get("AGENTTEST_LLM_BASE_URL", "")
        self.base_url = base.rstrip("/")
        self.model = model or os.environ.get(self.ENV_MODEL, "") or self.DEFAULT_MODEL
        self.timeout = timeout
        self._docs: Dict[str, str] = {}
        self._vectors: Dict[str, List[float]] = {}

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url)

    def build(self, docs: Dict[str, str]) -> None:
        self._docs = dict(docs)
        self._vectors = {}

    def add(self, doc_id: str, text: str) -> None:
        self._docs[doc_id] = text
        self._vectors.pop(doc_id, None)

    def remove(self, doc_id: str) -> None:
        self._docs.pop(doc_id, None)
        self._vectors.pop(doc_id, None)

    def indexed_ids(self) -> List[str]:
        return list(self._docs)

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        items = list(texts)
        if not self.available:
            raise EmbeddingUnavailable("未配置 embedding API Key / Base URL")
        if not items:
            return []
        import json
        from urllib import error as _error
        from urllib import request as _request

        payload = json.dumps({"model": self.model, "input": items}).encode("utf-8")
        req = _request.Request(
            f"{self.base_url}/embeddings",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with _request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (_error.URLError, _error.HTTPError, OSError, ValueError) as exc:
            raise EmbeddingUnavailable(f"embedding 接口调用失败: {exc}") from exc
        vectors = [item.get("embedding") or [] for item in (data.get("data") or [])]
        if len(vectors) != len(items) or any(not vec for vec in vectors):
            raise EmbeddingUnavailable("embedding 接口返回结构异常")
        return vectors

    def score_contributions(self, query: str) -> List[Tuple[str, float]]:
        if not self._docs:
            return []
        if not self._vectors:
            vectors = self.embed([self._docs[doc_id] for doc_id in self._docs])
            self._vectors = dict(zip(self._docs.keys(), vectors))
        query_vector = self.embed([query])[0]
        return [(doc_id, _cosine(query_vector, vec)) for doc_id, vec in self._vectors.items()]


class RagService:
    """语料 + 倒排索引 + 检索的完整链路（含缺陷注入与可选后端）。"""

    def __init__(
        self,
        bugs: Optional[Dict[str, bool]] = None,
        backend: Optional[str] = None,
        corpus: Optional[Sequence[Tuple[str, str]]] = None,
    ) -> None:
        self.bugs = dict(RAG_BUGS)
        if bugs:
            self.bugs.update({k: bool(v) for k, v in bugs.items() if k in RAG_BUGS})
        self.requested_backend = (backend or os.environ.get("AGENTTEST_RAG_BACKEND", "") or "bm25").strip().lower()
        self.fallback_reason = ""
        self.backend: Any
        self.backend, self.fallback_reason = self._make_backend()
        self.corpus: Dict[str, str] = {}
        for doc_id, text in (corpus if corpus is not None else DEFAULT_CORPUS):
            self.corpus[doc_id] = text
        self.version = 1
        self.backend.build(self.corpus)

    # ----- 后端选择（缺依赖/缺 Key 时优雅降级）-----

    def _make_backend(self) -> Tuple[Any, str]:
        if self.requested_backend == "embedding":
            embedding = EmbeddingBackend()
            if embedding.available:
                return embedding, ""
            return BM25Backend(self.bugs["cjk_tokenizer"]), "embedding 后端不可用（未配置 Key/Base URL），已降级为 BM25"
        return BM25Backend(self.bugs["cjk_tokenizer"]), ""

    # ----- 元信息 -----

    def size(self) -> int:
        """语料中的文档数（权威值）。"""
        return len(self.corpus)

    def index_size(self) -> int:
        """倒排索引中的文档数——与 size() 不一致即为索引陈旧。"""
        return len(self.backend.indexed_ids())

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": self.backend.name,
            "size": self.size(),
            "version": self.version,
            "doc_ids": sorted(self.corpus),
            "index_size": self.index_size(),
            "requested_backend": self.requested_backend,
            "fallback": self.fallback_reason,
        }

    # ----- 契约校验 -----

    def validate_search(self, q: Optional[str], top_k_raw: Optional[str]) -> Tuple[int, str]:
        """校验检索入参，返回 (top_k, error)。error 非空表示应按契约返回 400。"""
        if self.bugs["loose_contract"]:
            # 缺陷：空 q / 非法 top_k 不报错，静默按默认值继续检索
            try:
                top_k = int(top_k_raw) if top_k_raw not in (None, "") else RAG_DEFAULT_TOP_K
            except (TypeError, ValueError):
                top_k = RAG_DEFAULT_TOP_K
            if top_k <= 0:
                top_k = RAG_DEFAULT_TOP_K
            if top_k > RAG_MAX_TOP_K:
                top_k = RAG_MAX_TOP_K
            return top_k, ""
        if q is None or not str(q).strip():
            return RAG_DEFAULT_TOP_K, "q 不能为空"
        if len(str(q)) > RAG_MAX_QUERY_LEN:
            return RAG_DEFAULT_TOP_K, f"q 长度不能超过 {RAG_MAX_QUERY_LEN}"
        if top_k_raw in (None, ""):
            return RAG_DEFAULT_TOP_K, ""
        try:
            top_k = int(top_k_raw)
        except (TypeError, ValueError):
            return RAG_DEFAULT_TOP_K, "top_k 必须是正整数"
        if top_k <= 0:
            return RAG_DEFAULT_TOP_K, "top_k 必须是正整数"
        if top_k > RAG_MAX_TOP_K:
            return RAG_DEFAULT_TOP_K, f"top_k 不能超过 {RAG_MAX_TOP_K}"
        return top_k, ""

    # ----- 检索 -----

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        ranked = self._rank(self._contributions(query), top_k)
        return [
            {"doc_id": doc_id, "score": round(float(score), 6), "rank": rank,
             "text": self.corpus.get(doc_id, "")}
            for rank, (doc_id, score) in enumerate(ranked, start=1)
        ]

    def _contributions(self, query: str) -> List[Tuple[str, float]]:
        try:
            return self.backend.score_contributions(query)
        except EmbeddingUnavailable as exc:
            self.fallback_reason = f"embedding 检索失败（{exc}），本次请求降级为 BM25"
            self.backend = BM25Backend(self.bugs["cjk_tokenizer"])
            self.backend.build(self.corpus)
            return self.backend.score_contributions(query)

    def _rank(self, contributions: List[Tuple[str, float]], top_k: int) -> List[Tuple[str, float]]:
        if self.bugs["duplicate_hits"]:
            scored = [(doc_id, float(score)) for doc_id, score in contributions]
        else:
            aggregated: Dict[str, float] = {}
            for doc_id, score in contributions:
                aggregated[doc_id] = aggregated.get(doc_id, 0.0) + float(score)
            scored = list(aggregated.items())
        ascending = self.bugs["sort_asc"]
        if self.bugs["no_tiebreak"]:
            scored.sort(key=lambda item: item[1] if ascending else -item[1])
        else:
            scored.sort(key=lambda item: (item[1] if ascending else -item[1], item[0]))
        return scored[:max(int(top_k), 0)]

    # ----- 语料增删改（BUG_RAG_STALE_INDEX 的注入点）-----

    def add_doc(self, doc_id: str, text: str) -> None:
        self.corpus[doc_id] = text
        self.backend.add(doc_id, text)
        self.version += 1

    def remove_doc(self, doc_id: str) -> bool:
        if self.corpus.pop(doc_id, None) is None:
            return False
        if not self.bugs["stale_index"]:  # 缺陷：删除时忘记同步倒排索引
            self.backend.remove(doc_id)
        self.version += 1
        return True

    def update_doc(self, doc_id: str, text: str) -> bool:
        if doc_id not in self.corpus:
            return False
        self.corpus[doc_id] = text
        if not self.bugs["stale_index"]:  # 缺陷：更新时忘记重建该文档的倒排项
            self.backend.remove(doc_id)
            self.backend.add(doc_id, text)
        self.version += 1
        return True

    def reindex(self) -> int:
        """依据当前语料重建索引，返回新的 version（可用于修复索引不一致）。"""
        self.backend.build(self.corpus)
        self.version += 1
        return self.version
