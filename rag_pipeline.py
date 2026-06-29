"""
RAG pipeline for QA prompting.

Retrieves similar in-database QA examples. Two strategies:
- semantic: embedding cosine similarity (when an embedder is configured)
- lexical:  Jaccard token overlap (always available, the fallback)

``RAG_MODE`` selects the strategy ("auto" prefers semantic and falls back to
lexical). Example embeddings are backfilled lazily on first use and cached in
the database, so retrieval stays cheap after the first run.
"""

import json
import re
from typing import Dict, List, Optional

import config
from db_setup import RAGExample
from embeddings import EmbeddingError, cosine, get_embedder


class RAGPipeline:
    """Retrieves QA examples from SQLite for prompt augmentation."""

    def __init__(self, session, embedder="auto", mode: Optional[str] = None):
        self.session = session
        self.mode = (mode or config.RAG_MODE).lower()
        if embedder == "auto":
            embedder = None if self.mode == "lexical" else get_embedder()
        self.embedder = embedder

        if self.embedder is not None and self.mode != "lexical":
            self._ensure_embeddings()

    # --- shared ---
    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return [t for t in re.findall(r"[a-zA-Z0-9æøåÆØÅ]+", text.lower()) if len(t) > 2]

    @staticmethod
    def _searchable_text(example: RAGExample) -> str:
        return " ".join([
            example.title or "",
            example.topic or "",
            example.quality_label or "",
            example.report_excerpt or "",
            example.guidance or "",
        ])

    @staticmethod
    def _entry(example: RAGExample, score: float) -> Dict:
        return {
            "id": example.id,
            "title": example.title,
            "topic": example.topic,
            "quality_label": example.quality_label,
            "report_excerpt": example.report_excerpt,
            "guidance": example.guidance,
            "score": round(score, 4),
        }

    @staticmethod
    def _top(scored: List[Dict], top_k: int) -> List[Dict]:
        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:top_k]
        # If nothing matched at all, still return the best-effort top_k.
        if top and top[0]["score"] == 0:
            return top
        return [entry for entry in top if entry["score"] > 0]

    # --- semantic ---
    def _ensure_embeddings(self) -> None:
        """Embed and cache any examples missing a vector."""
        missing = [e for e in self.session.query(RAGExample).all() if not e.embedding]
        if not missing:
            return
        try:
            vectors = self.embedder.embed([self._searchable_text(e) for e in missing])
        except EmbeddingError:
            # Couldn't embed → disable semantic for this instance, use lexical.
            self.embedder = None
            return
        for example, vector in zip(missing, vectors):
            example.embedding = json.dumps(vector)
        self.session.commit()

    def _semantic(self, report_text: str, top_k: int) -> List[Dict]:
        query_vec = self.embedder.embed([report_text])[0]
        scored = []
        for example in self.session.query(RAGExample).all():
            if not example.embedding:
                continue
            scored.append(self._entry(example, cosine(query_vec, json.loads(example.embedding))))
        return self._top(scored, top_k)

    # --- lexical ---
    @classmethod
    def _overlap_score(cls, query_tokens: List[str], doc_tokens: List[str]) -> float:
        if not query_tokens or not doc_tokens:
            return 0.0
        qset, dset = set(query_tokens), set(doc_tokens)
        return len(qset & dset) / max(1, len(qset | dset))

    def _lexical(self, report_text: str, top_k: int) -> List[Dict]:
        query_tokens = self._tokenize(report_text)
        scored = [
            self._entry(example,
                        self._overlap_score(query_tokens,
                                            self._tokenize(self._searchable_text(example))))
            for example in self.session.query(RAGExample).all()
        ]
        return self._top(scored, top_k)

    # --- public ---
    def retrieve_examples(self, report_text: str, top_k: int = config.RAG_TOP_K) -> List[Dict]:
        """Return top-k similar examples (semantic if available, else lexical)."""
        if self.embedder is not None and self.mode != "lexical":
            try:
                return self._semantic(report_text, top_k)
            except EmbeddingError:
                pass  # network blip on the query → fall back to lexical
        return self._lexical(report_text, top_k)

    @staticmethod
    def build_context(examples: List[Dict]) -> str:
        """Build a compact context string for the LLM prompt."""
        if not examples:
            return "No matching examples found in retrieval database."

        blocks = []
        for idx, example in enumerate(examples, start=1):
            excerpt = (example["report_excerpt"] or "")[:600]
            blocks.append("\n".join([
                f"Example {idx} (score={example['score']}, quality={example['quality_label']})",
                f"Title: {example['title']}",
                f"Topic: {example['topic']}",
                f"Guidance: {example['guidance']}",
                f"Excerpt: {excerpt}",
            ]))
        return "\n\n".join(blocks)
