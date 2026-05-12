# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""


search

implementvector search + full-text search +.
corresponds to tasks.md 6.2.5-6.2.9.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

from .manager import MemoryEntry
from app.atlasclaw.core.user_paths import user_runtime_dir


@dataclass
class SearchResult:
    """

Search results
    
    Attributes:
        entry:memory entry
        score:(0-1)
        vector_score:
        text_score:
        recency_factor:sub
        diversity_penalty:multi
        highlights:list
    
"""
    entry: MemoryEntry
    score: float = 0.0
    vector_score: float = 0.0
    text_score: float = 0.0
    recency_factor: float = 1.0
    diversity_penalty: float = 0.0
    highlights: list[str] = field(default_factory=list)


class EmbeddingProvider(Protocol):
    """for"""
    
    async def embed(self, text: str) -> list[float]:
        """"""
        ...
        
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """"""
        ...


class HybridSearcher:
    """

search
    
    vector searchandfull-text search, apply and MMR multi.
    
    search:
    1. vector search:calculate
    2. full-text search:BM25
    3.:vector_weight * vector_score + text_weight * text_score
    4.:score * exp(-0.693 * age / half_life)
    5. MMR:multi entry
    
    Example usage::
    
        searcher = HybridSearcher(embedding_provider=my_provider)
        
        #
        for entry in entries:
            await searcher.index(entry)
            
        # search
        results = await searcher.search(" management", to p_k=5)
    
"""
    
    def __init__(
        self,
        *,
        embedding_provider: Optional[EmbeddingProvider] = None,
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
        half_life_days: float = 30.0,
        mmr_lambda: float = 0.7,
        user_id: str = "default",
        workspace: str = "",
    ) -> None:
        """


initialize search
 
 Args:
 embedding_provider:for(optional, vector search)
 vector_weight:vector search(default 0.7)
 text_weight:full-text search(default 0.3)
 half_life_days:()
 mmr_lambda:MMR multi parameter(0-1,, multi)
 user_id: User identifier for per-user index isolation.
 workspace: Workspace root path (used to derive index path for sqlite-vec).
 
"""
        self._embedding_provider = embedding_provider
        self._vector_weight = vector_weight
        self._text_weight = text_weight
        self._half_life_days = half_life_days
        self._mmr_lambda = mmr_lambda
        self._user_id = user_id
        
        # Path for sqlite-vec persistent index (used when sqlite-vec is available)
        if workspace:
            from pathlib import Path
            self._index_path: Optional[str] = str(
                user_runtime_dir(Path(workspace), user_id) / "memory" / "index.sqlite"
            )
        else:
            self._index_path = None
        
        # In-memory index (primary storage; sqlite-vec degrades to this when unavailable)
        self._entries: dict[str, MemoryEntry] = {}
        self._embeddings: dict[str, list[float]] = {}
        
        # (used for BM25)
        self._doc_count = 0
        self._doc_lengths: dict[str, int] = {}
        self._avg_doc_length = 0.0
        self._term_doc_freq: dict[str, int] = {}
        
    async def index(self, entry: MemoryEntry) -> None:
        """

memory entry
        
        Args:
            entry:memory entry
        
"""
        self._entries[entry.id] = entry
        
        # (such as for)
        if self._embedding_provider:
            if entry.embedding:
                self._embeddings[entry.id] = entry.embedding
            else:
                embedding = await self._embedding_provider.embed(entry.content)
                self._embeddings[entry.id] = embedding
                entry.embedding = embedding
                
        # 
        self._update_term_stats(entry)
        
    def index_sync(self, entry: MemoryEntry, embedding: Optional[list[float]] = None) -> None:
        """

memory entry()
        
        Args:
            entry:memory entry
            embedding:calculate(optional)
        
"""
        self._entries[entry.id] = entry
        
        if embedding:
            self._embeddings[entry.id] = embedding
            entry.embedding = embedding
        elif entry.embedding:
            self._embeddings[entry.id] = entry.embedding
            
        self._update_term_stats(entry)
        
    def _update_term_stats(self, entry: MemoryEntry) -> None:
        """"""
        tokens = self._tokenize(entry.content)
        doc_length = len(tokens)
        
        self._doc_count += 1
        self._doc_lengths[entry.id] = doc_length
        
        # 
        total_length = sum(self._doc_lengths.values())
        self._avg_doc_length = total_length / self._doc_count if self._doc_count > 0 else 0
        
        # 
        seen_terms: set[str] = set()
        for token in tokens:
            if token not in seen_terms:
                self._term_doc_freq[token] = self._term_doc_freq.get(token, 0) + 1
                seen_terms.add(token)
                
    def remove(self, entry_id: str) -> bool:
        """

from in entry
        
        Args:
            entry_id:entry ID
            
        Returns:
            
        
"""
        if entry_id in self._entries:
            del self._entries[entry_id]
            self._embeddings.pop(entry_id, None)
            self._doc_lengths.pop(entry_id, None)
            self._doc_count = max(0, self._doc_count - 1)
            return True
        return False
        
    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        filter_fn: Optional[Callable[[MemoryEntry], bool]] = None,
        apply_recency: bool = True,
        apply_mmr: bool = True,
    ) -> list[SearchResult]:
        """

execute search
        
        Args:
            query:
            to p_k:return count
            filter_fn:filter count(optional)
            apply_recency:apply
            apply_mmr:apply MMR
            
        Returns:
            Search resultslist()
        
"""
        if not self._entries:
            return []
            
        # 
        query_embedding: Optional[list[float]] = None
        if self._embedding_provider and self._embeddings:
            query_embedding = await self._embedding_provider.embed(query)
            
        # calculate entry
        results: list[SearchResult] = []
        
        for entry_id, entry in self._entries.items():
            # applyfilter
            if filter_fn and not filter_fn(entry):
                continue
                
            # 
            vector_score = 0.0
            if query_embedding and entry_id in self._embeddings:
                vector_score = self._cosine_similarity(
                    query_embedding, self._embeddings[entry_id]
                )
                
            # 
            text_score = self._bm25_score(query, entry)
            
            # 
            if query_embedding and self._embeddings:
                score = (self._vector_weight * vector_score + 
                        self._text_weight * text_score)
            else:
                # vector search use
                score = text_score
                
            # 
            recency_factor = 1.0
            if apply_recency:
                recency_factor = self._calculate_recency(entry.timestamp)
                score *= recency_factor
                
            # 
            highlights = self._generate_highlights(query, entry.content)
            
            results.append(SearchResult(
                entry=entry,
                score=score,
                vector_score=vector_score,
                text_score=text_score,
                recency_factor=recency_factor,
                highlights=highlights
            ))
            
        # 
        results.sort(key=lambda r: r.score, reverse=True)
        
        # MMR
        if apply_mmr and len(results) > top_k:
            results = self._apply_mmr(results, top_k)
        else:
            results = results[:top_k]
            
        return results
        
    def _tokenize(self, text: str) -> list[str]:
        """


        
        implement:and split,.
        use(such as jieba).
        
"""
        # 
        text = re.sub(r'[^\w\s]', ' ', text.lower())
        # split filter
        tokens = [t.strip() for t in text.split() if t.strip()]
        return tokens
        
    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """calculate"""
        if len(vec1) != len(vec2):
            return 0.0
            
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
            
        return dot_product / (norm1 * norm2)
        
    def _bm25_score(self, query: str, entry: MemoryEntry) -> float:
        """

calculate BM25
        
        BM25 parameter:k1=1.5, b=0.75
        
"""
        k1 = 1.5
        b = 0.75
        
        query_tokens = self._tokenize(query)
        doc_tokens = self._tokenize(entry.content)
        doc_length = len(doc_tokens)
        
        if doc_length == 0 or self._avg_doc_length == 0:
            return 0.0
            
        # calculate in
        term_freq: dict[str, int] = {}
        for token in doc_tokens:
            term_freq[token] = term_freq.get(token, 0) + 1
            
        score = 0.0
        for token in query_tokens:
            if token not in term_freq:
                continue
                
            # IDF
            df = self._term_doc_freq.get(token, 0)
            idf = math.log((self._doc_count - df + 0.5) / (df + 0.5) + 1)
            
            # TF
            tf = term_freq[token]
            tf_component = (tf * (k1 + 1)) / (
                tf + k1 * (1 - b + b * doc_length / self._avg_doc_length)
            )
            
            score += idf * tf_component
            
        # to 0-1
        max_score = len(query_tokens) * 5  # count
        return min(score / max_score, 1.0) if max_score > 0 else 0.0
        
    def _calculate_recency(self, timestamp: datetime) -> float:
        """

calculate sub
        
        use count:exp(-0.693 * age / half_life)
        to 0.5
        
"""
        now = datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
            
        age_days = (now - timestamp).total_seconds() / 86400.0
        
        if age_days <= 0:
            return 1.0
            
        decay = math.exp(-0.693 * age_days / self._half_life_days)
        return max(decay, 0.01)  # 1%
        
    def _generate_highlights(self, query: str, content: str, context_chars: int = 50) -> list[str]:
        """"""
        highlights = []
        query_tokens = set(self._tokenize(query))
        
        # contains sub
        sentences = re.split(r'[.。!！?？\n]', content)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
                
            sentence_tokens = set(self._tokenize(sentence))
            if query_tokens & sentence_tokens:
                # 
                if len(sentence) > context_chars * 2:
                    # to
                    for token in query_tokens:
                        pos = sentence.lower().find(token)
                        if pos >= 0:
                            start = max(0, pos - context_chars)
                            end = min(len(sentence), pos + len(token) + context_chars)
                            snippet = sentence[start:end]
                            if start > 0:
                                snippet = "..." + snippet
                            if end < len(sentence):
                                snippet = snippet + "..."
                            highlights.append(snippet)
                            break
                else:
                    highlights.append(sentence)
                    
            if len(highlights) >= 3:
                break
                
        return highlights
        
    def _apply_mmr(self, results: list[SearchResult], top_k: int) -> list[SearchResult]:
        """

apply MMR(Maximal Marginal Relevance)
        
        MMR = λ * sim(q, d) -(1-λ) * max(sim(d, d_selected))
        
"""
        if not results:
            return []
            
        selected: list[SearchResult] = [results[0]]
        remaining = results[1:]
        
        while len(selected) < top_k and remaining:
            best_mmr = float('-inf')
            best_idx = 0
            
            for i, candidate in enumerate(remaining):
                # 
                relevance = candidate.score
                
                # 
                max_similarity = 0.0
                if candidate.entry.id in self._embeddings:
                    cand_emb = self._embeddings[candidate.entry.id]
                    for sel in selected:
                        if sel.entry.id in self._embeddings:
                            sel_emb = self._embeddings[sel.entry.id]
                            sim = self._cosine_similarity(cand_emb, sel_emb)
                            max_similarity = max(max_similarity, sim)
                            
                # MMR
                mmr = self._mmr_lambda * relevance - (1 - self._mmr_lambda) * max_similarity
                
                if mmr > best_mmr:
                    best_mmr = mmr
                    best_idx = i
                    
            selected.append(remaining.pop(best_idx))
            
        return selected
        
    def clear(self) -> None:
        """"""
        self._entries.clear()
        self._embeddings.clear()
        self._doc_count = 0
        self._doc_lengths.clear()
        self._avg_doc_length = 0.0
        self._term_doc_freq.clear()
