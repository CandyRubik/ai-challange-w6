"""The shared benchmark executes retrieval and grounded generation without a chat product."""

import json
from types import SimpleNamespace

from app.indexing.store import SearchHit
from scripts import run_day28_evaluation as benchmark


def test_day28_runs_on_shared_retrieval_and_writes_a_complete_report(tmp_path, monkeypatch):
    class Model:
        def generate_json(self, *, messages, **kwargs):
            if messages[0]["content"].startswith("Rewrite"):
                return '{"query": "ChatSessionService"}'
            return json.dumps({"status": "answered", "claims": [{
                "text": "Сервис управляет чатами.", "evidence_ids": ["docs-0001:q0"],
            }]})
        generate = generate_json

    class Registry:
        def resolve(self, provider):
            return SimpleNamespace(model="test-model")
        def build(self, selection, **kwargs):
            return Model()

    class Index:
        def ensure_built(self):
            return {"fingerprint": "test-corpus", "version": "test"}
        def search(self, query, strategy="structure", k=3):
            return [SearchHit({"chunk_id": "docs-0001", "source": "README.md", "section": "ChatSessionService",
                               "title": "Project", "line_start": 1, "line_end": 1,
                               "text": "ChatSessionService manages chat sessions."}, 1.0)]

    class Reranker:
        def score(self, question, hits):
            return [1.0 for _ in hits]

    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    monkeypatch.setattr(benchmark, "ModelRegistry", Registry)
    monkeypatch.setattr(benchmark, "DocumentIndex", Index)
    monkeypatch.setattr(benchmark, "LocalCrossEncoderReranker", Reranker)
    monkeypatch.setattr(benchmark, "version", lambda name: "test")
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": 1, "question": "Что делает ChatSessionService?", "expected_status": "answered"}]))
    target = tmp_path / "report.json"
    result = benchmark.run(target, 1, ["ollama"], questions)
    assert result["complete"]
    assert result["results"][0]["search_question"] == "ChatSessionService"
    assert result["results"][0]["runs"][0]["status"] == "answered"
    assert result["summary"]["ollama"]["automatic_passes"] == 1
    assert json.loads(target.read_text())["complete"]
