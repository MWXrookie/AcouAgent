"""Knowledge base loading + retrieval (replaces Dify's 知识检索 / 模板转换).

Two interchangeable backends:

* ``bm25``   – pure-python lexical search (default). No network, no model
  download, no extra dependencies; good enough for a single hand-curated
  markdown file and fully portable.
* ``openai`` – OpenAI-compatible embeddings + ``InMemoryVectorStore``.

The original Dify node used a hosted vector store with ``top_k=4`` and a
``qwen3-rerank`` reranker; ``top_k`` is preserved here and reranking is only
attempted when an embeddings backend (which can score similarity) is in use.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field, PrivateAttr

from .config import Settings

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_ASCII_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


# --------------------------------------------------------------------------- #
# tokenisation
# --------------------------------------------------------------------------- #
def tokenize(text: str) -> list[str]:
    """Language-agnostic tokeniser.

    Latin words/digits are kept whole, CJK text is indexed both as single
    characters and as character bigrams (a cheap, dependency-free approximation
    of Chinese word segmentation that works well for BM25).
    """
    lowered = text.lower()
    tokens: list[str] = _ASCII_RE.findall(lowered)
    cjk = _CJK_RE.findall(lowered)
    tokens.extend(cjk)
    tokens.extend(a + b for a, b in zip(cjk, cjk[1:]))
    return tokens


# --------------------------------------------------------------------------- #
# markdown chunking
# --------------------------------------------------------------------------- #
def split_markdown(text: str, min_chars: int = 40) -> list[str]:
    """Split a markdown document on ``###``-style headings.

    Each chunk keeps its heading so the retriever output stays self-describing
    (this replaces Dify's 模板转换 node, which concatenated ``item.content``).
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [text.strip()]

    preamble = text[: matches[0].start()].strip()
    chunks: list[str] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.start() : end].strip()
        if body:
            chunks.append(body)

    # fold a tiny leading preamble (the document title) into the first chunk
    if preamble:
        if chunks and len(preamble) < min_chars:
            chunks[0] = f"{preamble}\n\n{chunks[0]}"
        else:
            chunks.insert(0, preamble)
    return [c for c in chunks if c.strip()]


def load_knowledge_base(path: Path) -> list[Document]:
    """Read the markdown knowledge base and turn it into LangChain Documents."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"knowledge base not found: {path}")
    text = path.read_text(encoding="utf-8")
    return [
        Document(page_content=chunk, metadata={"source": str(path), "chunk": i})
        for i, chunk in enumerate(split_markdown(text))
    ]


# --------------------------------------------------------------------------- #
# BM25
# --------------------------------------------------------------------------- #
class BM25:
    """Classic Okapi BM25 (self-contained; no external dependency)."""

    def __init__(self, corpus: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75):
        self.corpus = [list(doc) for doc in corpus]
        self.k1 = k1
        self.b = b
        self.n = len(self.corpus)
        self.doc_len = [len(doc) for doc in self.corpus]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.freqs = [Counter(doc) for doc in self.corpus]

        df: Counter = Counter()
        for doc in self.corpus:
            df.update(set(doc))
        self.idf = {
            term: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def score(self, query: Sequence[str]) -> list[float]:
        scores = [0.0] * self.n
        if not self.n or not self.avgdl:
            return scores
        for term in query:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, freq in enumerate(self.freqs):
                tf = freq.get(term)
                if not tf:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                scores[i] += idf * tf * (self.k1 + 1) / denom
        return scores


class BM25Retriever(BaseRetriever):
    """LangChain retriever backed by the BM25 index above."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    documents: list[Document] = Field(default_factory=list)
    k: int = 4
    _index: BM25 = PrivateAttr()
    _tokenized: list[list[str]] = PrivateAttr()

    def __init__(self, documents: Iterable[Document], k: int = 4, **kwargs):
        docs = list(documents)
        super().__init__(documents=docs, k=k, **kwargs)
        self._tokenized = [tokenize(d.page_content) for d in docs]
        self._index = BM25(self._tokenized)

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:
        scores = self._index.score(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        ranked = [i for i in ranked if scores[i] > 0][: self.k]
        if not ranked:  # never return nothing: fall back to the first k chunks
            ranked = list(range(min(self.k, len(self.documents))))
        return [self.documents[i] for i in ranked]


# --------------------------------------------------------------------------- #
# embeddings backend (optional)
# --------------------------------------------------------------------------- #
def _build_embedding_retriever(settings: Settings, documents: list[Document], k: int):
    from langchain_core.vectorstores import InMemoryVectorStore
    from langchain_openai import OpenAIEmbeddings

    if not settings.openai_api_key:
        raise RuntimeError("retrieval_backend='openai' requires an API key")
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    store = InMemoryVectorStore(embedding=embeddings)
    store.add_documents(documents)
    return store.as_retriever(search_kwargs={"k": k})


# --------------------------------------------------------------------------- #
# public factory
# --------------------------------------------------------------------------- #
def build_retriever(settings: Settings) -> BaseRetriever:
    documents = load_knowledge_base(settings.resolved_kb_path())
    backend = (settings.retrieval_backend or "bm25").lower()
    if backend == "openai":
        return _build_embedding_retriever(settings, documents, settings.top_k)
    if backend != "bm25":
        raise ValueError(f"unknown retrieval backend: {settings.retrieval_backend!r}")
    return BM25Retriever(documents, k=settings.top_k)


def render_template_transform(documents: Sequence[Document]) -> str:
    """Reproduce Dify's 模板转换 node.

    Dify ran ``{% for item in arg1 %}{{ item.content }}{% endfor %}`` over the
    retrieval result, i.e. a plain newline-joined concatenation.
    """
    return "".join(f"{doc.page_content}\n" for doc in documents)
