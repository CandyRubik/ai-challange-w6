"""Offline weights and shared document quotation contracts."""

import json
import sys
from types import SimpleNamespace

import pytest

from app.indexing.store import DocumentIndex, DocumentIndexError
from app.indexing.rerank import LocalCrossEncoderReranker
from app.indexing.citation_options import prepare_citations, resolve_citations
from app.indexing.grounding import GroundingError, validate_grounded_answer
from tests.rag_fixtures import Index


def test_embedding_and_reranker_never_download_missing_weights(tmp_path, monkeypatch):
    calls = []
    def missing(*args, **kwargs):
        calls.append(kwargs)
        raise OSError("missing cache")
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=missing, CrossEncoder=missing))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda n: None, nn=SimpleNamespace(Sigmoid=lambda: None)))
    with pytest.raises(DocumentIndexError, match="Локальные веса"):
        DocumentIndex(tmp_path)._model()
    with pytest.raises(DocumentIndexError, match="Локальные веса"):
        LocalCrossEncoderReranker().score("ChatSessionService", Index().search("", "structure", 1))
    assert len(calls) == 2 and all(c["local_files_only"] is True for c in calls)


def test_index_rejects_a_different_embedding_model(tmp_path):
    (tmp_path / "active.json").write_text(json.dumps({"model": "different-e5", "version": "old", "corpus": "project-docs-v1"}))
    with pytest.raises(DocumentIndexError, match="другой моделью"):
        DocumentIndex(tmp_path).status()


def test_quote_selection_attaches_exact_document_text():
    hits = Index().search("ChatSessionService", "structure", 1)
    prompt, schema, lookup = prepare_citations(hits)
    key = next(iter(lookup))
    assert key in schema["$defs"]["CitedClaim"]["properties"]["evidence_ids"]["items"]["enum"]
    raw = json.dumps({"status": "answered", "claims": [{"text": "ChatSessionService выполняет задачи.", "evidence_ids": [key]}]})
    answer = validate_grounded_answer(resolve_citations(raw, lookup), hits)
    assert answer.citations[0]["quote"] == hits[0].metadata["text"]
    with pytest.raises(GroundingError):
        resolve_citations(raw.replace(key, "invented-quote"), lookup)


def test_old_draft_cannot_bypass_exact_quote_validation():
    hits = Index().search("", "structure", 1)
    _, _, lookup = prepare_citations(hits)
    raw = json.dumps({"status": "answered", "claims": [{"text": "Выдумка", "evidence": [{
        "chunk_id": hits[0].metadata["chunk_id"], "quote": "Invented sentence not present in the documentation.",
    }]}]})
    with pytest.raises(GroundingError):
        validate_grounded_answer(resolve_citations(raw, lookup), hits)
