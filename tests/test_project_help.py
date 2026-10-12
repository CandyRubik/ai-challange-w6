"""Documentation-only corpus, live MCP, and /help's durable chat behavior."""

import hashlib
import json
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
import numpy as np
import pytest

from app.agents.agent import Agent
from app.indexing.chunking import fixed_chunks, structured_chunks
from app.indexing.corpus import load_documents
from app.indexing.rag import DocumentRag, RetrievalSettings
from app.indexing.store import DocumentIndex, SearchHit
from app.main import app, get_chat_session_service, get_document_index
from app.memory.service import SQLiteMemoryRepository
from app.project_help.mcp_client import ProjectMcpClient
from app.project_help.service import ProjectHelpService, help_question
from app.providers.errors import LlmRequestError
from app.services.chat_sessions import ChatSessionService
from app.storage.chat_sessions import SQLiteChatSessionRepository


class Tokens:
    def encode(self, text, **kwargs):
        return list(text)


class Embedder:
    tokenizer = Tokens()
    def encode(self, sentences, **kwargs):
        return np.asarray([[1, .1, .2] for _ in sentences], dtype="float32")


def corpus(root):
    root.mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "README.md").write_text("# Project\n\n## Structure\nBackend lives in app/.\n", encoding="utf-8")
    (root / "docs/memory.md").write_text("# Memory\nChat sessions use SQLite.\n", encoding="utf-8")
    return root


def test_corpus_is_only_readme_and_docs_and_ignores_external_symlinks(tmp_path):
    root = corpus(tmp_path / "project")
    (root / ".env").write_text("SECRET=value")
    (root / "docs/data.bin").write_bytes(b"unrelated")
    (root / "app.py").write_text("source code")
    outside = tmp_path / "private.md"
    outside.write_text("private")
    (root / "docs/link.md").symlink_to(outside)
    assert [doc.path for doc in load_documents(root)] == ["README.md", "docs/memory.md"]


def test_chunks_keep_exact_text_lines_headings_and_fenced_code(tmp_path):
    root = corpus(tmp_path)
    text = "# Project\n\n## Setup\n```sh\n# not a heading\nuvicorn app.main:app\n```\n" + "long line " * 80
    (root / "README.md").write_text(text)
    documents = load_documents(root)
    for chunks in [structured_chunks(documents, Tokens(), size=120), fixed_chunks(documents, Tokens(), size=120, overlap=20)]:
        assert chunks
        for chunk in chunks:
            original = next(doc for doc in documents if doc.path == chunk.source)
            assert chunk.text in original.text
            lines = original.text.splitlines(keepends=True)
            assert chunk.text in "".join(lines[chunk.line_start - 1:chunk.line_end])
            assert chunk.token_count <= 120
            assert "not a heading" not in chunk.section


def test_index_rebuilds_for_modified_added_and_deleted_documents(tmp_path):
    root = corpus(tmp_path / "project")
    index = DocumentIndex(tmp_path / "index", root, Embedder())
    first = index.ensure_built()
    assert set(first["files"]) == {"README.md", "docs/memory.md"}
    assert index.ensure_built()["version"] == first["version"]
    (root / "docs/memory.md").write_text("# Memory\nNow contains more documentation.\n")
    assert index.status()["stale"]
    second = index.ensure_built()
    assert second["version"] != first["version"] and not index.status()["stale"]
    (root / "docs/api.md").write_text("# API\nGET /api/health\n")
    assert "docs/api.md" in index.ensure_built()["files"]
    (root / "docs/memory.md").unlink()
    final = index.ensure_built()
    assert "docs/memory.md" not in final["files"]
    assert index.search("API")[0].metadata["source"] in final["files"]


def test_old_corpus_manifest_cannot_be_used_as_project_index(tmp_path):
    (tmp_path / "active.json").write_text(json.dumps({"version": "old", "model": "old-model"}))
    assert DocumentIndex(tmp_path).status()["state"] == "missing"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_real_mcp_roundtrip_refreshes_branch_and_handles_detached_head(tmp_path):
    git(tmp_path, "init", "--initial-branch=main")
    (tmp_path / "README.md").write_text("docs")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "Initial")
    client = ProjectMcpClient(tmp_path)
    first = client.branch()
    assert first["status"] == "ok" and first["branch"] == "main"
    assert first["commit"] == git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "checkout", "-b", "feature/help")
    assert client.branch()["branch"] == "feature/help"
    git(tmp_path, "checkout", "--detach")
    detached = client.branch()
    assert detached["status"] == "ok" and detached["detached"] and detached["branch"] is None


def test_real_mcp_missing_checkout_is_unavailable(tmp_path):
    result = ProjectMcpClient(tmp_path).branch()
    assert result["status"] == "unavailable" and "branch" not in result


class Mcp:
    calls = 0
    def branch(self):
        self.calls += 1
        return {"tool": "git_branch", "transport": "stdio", "status": "ok", "repository": "project",
                "branch": f"feature-{self.calls}", "commit": "a" * 40, "detached": False,
                "observed_at": "2026-10-12T00:00:00+00:00"}


class Index:
    def __init__(self, empty=False):
        self.empty = empty
    def ensure_built(self):
        return {"version": "docs-v1"}
    def status(self):
        return {"index": {"version": "docs-v1"}}
    def search(self, *args, **kwargs):
        return [] if self.empty else [SearchHit({
            "chunk_id": "structure-0001", "source": "docs/memory.md", "title": "Project",
            "section": "Memory", "line_start": 1, "line_end": 2, "text": "Chat sessions use SQLite.",
            "sha256": "b" * 64,
        }, .9)]


class Model:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail
    def generate_json(self, *, messages, **kwargs):
        self.calls += 1
        if self.fail:
            raise LlmRequestError("Temporary model error")
        prompt = messages[0]["content"]
        assert "USER_PROFILE" not in prompt or "Project" in prompt
        options, _ = json.JSONDecoder().raw_decode(prompt.split("DOCUMENT_EXCERPTS:\n", 1)[1])
        if len(options) > 1:
            assert options[1]["source"] == "MCP:git_branch"
        return json.dumps({"status": "answered", "claims": [{
            "text": "Чат-сессии хранятся в SQLite.", "evidence_ids": [options[0]["quote_options"][0]["id"]],
        }]})
    def generate(self, **kwargs):
        raise AssertionError("Ordinary chat generation must not run for /help")


class NeverMemory:
    def interpret(self, *args):
        raise AssertionError("/help must not update memory")
    def extract(self, *args, **kwargs):
        raise AssertionError("/help must not extract memory")


def help_service(tmp_path, model=None, index=None, mcp=None):
    repository = SQLiteChatSessionRepository(tmp_path / "chat.sqlite3")
    memories = SQLiteMemoryRepository(tmp_path / "chat.sqlite3")
    model = model or Model()
    helper = ProjectHelpService(DocumentRag(index or Index(), model, settings=RetrievalSettings(
        rewrite=False, filter_relevance=False)), mcp or Mcp())
    service = ChatSessionService(repository, Agent(model), memory_repository=memories,
                                 memory_extractor=NeverMemory(), memory_interpreter=NeverMemory(),
                                 project_help=helper)
    return service, repository, memories, model


@pytest.mark.parametrize("content", ["/help", "/help\nГде память?", " /help Какая ветка? "])
def test_help_command_parser(content):
    assert help_question(content)


@pytest.mark.parametrize("content", ["/helper", "Расскажи про /help", "/helpful question"])
def test_help_parser_does_not_route_other_messages(content):
    assert help_question(content) is None


def test_api_help_persists_sources_and_mcp_after_restart_without_memory_changes(tmp_path):
    service, repository, memories, model = help_service(tmp_path)
    session = service.create()
    app.dependency_overrides[get_chat_session_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(f"/api/chat/sessions/{session.id}/messages", json={"content": "/help Где память?"})
            assert response.status_code == 200, response.text
            message = response.json()["assistant_message"]
            details = message["project_help"]
            assert details["sources"][0]["source"] == "docs/memory.md"
            assert details["citations"][0]["quote"] == "Chat sessions use SQLite."
            assert details["mcp"]["branch"] == "feature-1" and details["index_version"] == "docs-v1"
        restored = SQLiteChatSessionRepository(tmp_path / "chat.sqlite3").get(session.id)
        assert restored.messages[-1].project_help == details
        assert model.calls == 1 and memories.list_working(session.id) == []
    finally:
        app.dependency_overrides.clear()


def test_help_during_active_task_does_not_advance_state(tmp_path):
    service, repository, _, _ = help_service(tmp_path)
    session = service.create()
    before = service.start_task(session.id, "Подготовить план")
    answered = service.send(session.id, "/help Какая сейчас ветка?")
    assert answered.assistant_message.project_help["mcp"]["branch"] == "feature-1"
    assert service.get(session.id).task == before.task


def test_exact_branch_question_skips_models_and_document_index(tmp_path):
    service, _, _, model = help_service(tmp_path, index=Index(empty=True))
    session = service.create()
    first = service.send(session.id, "/help Какая сейчас ветка?")
    second = service.send(session.id, "/help Какая сейчас ветка?")
    assert "feature-1" in first.assistant_message.content and "feature-2" in second.assistant_message.content
    assert model.calls == 0


def test_retry_recognizes_saved_help_and_refreshes_git_context(tmp_path):
    mcp = Mcp()
    service, _, _, _ = help_service(tmp_path, model=Model(fail=True), mcp=mcp)
    session = service.create()
    with pytest.raises(LlmRequestError):
        service.send(session.id, "/help Где память?")
    pending = service.get(session.id).messages[-1]
    assert pending.status == "failed" and pending.project_help["question"] == "Где память?"
    restarted, _, _, _ = help_service(tmp_path, mcp=mcp)
    result = restarted.retry(session.id, pending.id)
    assert result.user_message.id == pending.id
    assert result.assistant_message.project_help["mcp"]["branch"] == "feature-2"


def test_no_document_hits_does_not_generate_from_git_alone(tmp_path):
    service, _, _, model = help_service(tmp_path, index=Index(empty=True))
    response = service.send(service.create().id, "/help Как настроен Apache Spark?")
    assert response.assistant_message.project_help["grounding_status"] == "insufficient_context"
    assert model.calls == 0 and not response.assistant_message.project_help["sources"]


def test_source_endpoint_line_anchor_hash_check_html_escaping_and_path_boundary(tmp_path):
    root = corpus(tmp_path)
    (root / "README.md").write_text("# Project\n<script>alert(1)</script>\n")
    digest = hashlib.sha256((root / "README.md").read_bytes()).hexdigest()
    app.dependency_overrides.clear()
    # The source endpoint uses the shared cached index factory.
    from app import main
    original = main.get_document_index
    main.get_document_index = lambda: DocumentIndex(tmp_path / "index", root)
    try:
        with TestClient(app) as client:
            good = client.get("/api/document-index/source", params={"path": "README.md", "sha256": digest})
            assert good.status_code == 200 and 'id="L2"' in good.text
            assert "&lt;script&gt;" in good.text and "<script>" not in good.text
            assert client.get("/api/document-index/source", params={"path": "../.env"}).status_code == 404
            (root / "README.md").write_text("changed")
            assert client.get("/api/document-index/source", params={"path": "README.md", "sha256": digest}).status_code == 409
    finally:
        main.get_document_index = original


def test_help_regenerates_overlong_answer_and_keeps_exact_source_quotes(tmp_path):
    from app.invariants import SQLiteInvariantRepository, sentence_count, emoji_count
    class OverlongFirst(Model):
        def generate_json(self, **kwargs):
            raw = json.loads(super().generate_json(**kwargs))
            if self.calls == 1:
                raw["claims"] *= 4
            return json.dumps(raw)
    service, repository, _, model = help_service(tmp_path, model=OverlongFirst())
    service._invariants = SQLiteInvariantRepository(tmp_path / "chat.sqlite3")
    answer = service.send(service.create().id, "/help Где память?").assistant_message
    assert not answer.refusal and model.calls == 2
    assert sentence_count(answer.content) <= 3 and emoji_count(answer.content) >= 3
    assert answer.project_help["citations"][0]["quote"] == "Chat sessions use SQLite."
    assert answer.project_help["metrics"]["generation_attempts"] == 2


def test_sentence_limit_counts_filenames_as_words():
    from app.invariants import sentence_count
    assert sentence_count("Backend: app/main.py. UI: static/app.js. Docs: README.md.") == 3
    assert sentence_count("Первое.Второе!Третье?") == 3


def test_document_answers_survive_unavailable_mcp(tmp_path):
    class Unavailable:
        def branch(self):
            return {"tool": "git_branch", "status": "unavailable", "error": "MCP недоступен"}
    service, _, _, model = help_service(tmp_path, mcp=Unavailable())
    response = service.send(service.create().id, "/help Где память?").assistant_message
    assert response.project_help["grounding_status"] == "answered"
    assert response.project_help["mcp"]["status"] == "unavailable"
    assert response.project_help["sources"][0]["kind"] == "document"


def test_help_retry_pins_provider_and_model_after_preference_and_config_changes(tmp_path):
    from app.providers.registry import ModelRegistry
    class Registry(ModelRegistry):
        def __init__(self, model):
            super().__init__()
            self.model = model
            self.selections = []
        def build(self, selection, **kwargs):
            self.selections.append(selection)
            return self.model
    model = Model(fail=True)
    registry = Registry(model)
    repository = SQLiteChatSessionRepository(tmp_path / "chat.sqlite3")
    helper = ProjectHelpService(DocumentRag(Index(), model, settings=RetrievalSettings(
        rewrite=False, filter_relevance=False)), Mcp())
    service = ChatSessionService(repository, model_registry=registry, project_help=helper)
    session = service.create(provider="ollama")
    original_model = registry.resolve("ollama").model
    with pytest.raises(LlmRequestError):
        service.send(session.id, "/help Где память?")
    pending = service.get(session.id).messages[-1]
    repository.set_provider(session.id, "deepseek")
    registry._models["ollama"] = "changed-local-model"
    registry.selections.clear()
    model.fail = False
    response = service.retry(session.id, pending.id)
    assert response.assistant_message.provider == "ollama"
    assert response.assistant_message.model == original_model
    assert all(selection.provider == "ollama" and selection.model == original_model for selection in registry.selections)
