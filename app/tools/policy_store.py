"""Policy knowledge base: Markdown policies chunked by clause, retrieved with BM25.

Clause IDs (WAR-2.1, TRN-2.3 ...) are stable, so rule results and retrieved text cite the same identifiers.
An embedding retriever can be added behind `search` for hybrid retrieval; BM25 keeps the demo dependency-free.
"""
from __future__ import annotations

import re
from functools import lru_cache

from rank_bm25 import BM25Okapi

from app.config import POLICY_DIR


def _tok(s: str) -> list[str]:
    return re.findall(r"[a-z0-9\-\.]+", s.lower())


@lru_cache
def clauses() -> list[dict]:
    out = []
    for path in sorted(POLICY_DIR.glob("*.md")):
        doc_title = path.read_text().splitlines()[0].lstrip("# ").strip()
        for m in re.finditer(r"^### (\S+) (.+?)\n(.*?)(?=^### |\Z)", path.read_text(), re.S | re.M):
            out.append({"clause_id": m.group(1), "title": m.group(2).strip(), "text": m.group(3).strip(),
                        "document": doc_title})
    return out


@lru_cache
def _index() -> BM25Okapi:
    return BM25Okapi([_tok(f"{c['clause_id']} {c['title']} {c['text']}") for c in clauses()])


def search(query: str, k: int = 3) -> list[dict]:
    exact = [c for c in clauses() if c["clause_id"].lower() in query.lower()]
    scores = _index().get_scores(_tok(query))
    ranked = sorted(zip(scores, clauses()), key=lambda x: -x[0])
    hits = exact + [c for s, c in ranked if s > 0 and c not in exact]
    return hits[:k]


def get_clause(clause_id: str) -> dict | None:
    return next((c for c in clauses() if c["clause_id"] == clause_id), None)
