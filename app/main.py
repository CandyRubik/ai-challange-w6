from __future__ import annotations

from functools import lru_cache
from html import escape
import os
from pathlib import Path
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .agents.agent import AgentInputError, AgentOutputError
from .indexing.corpus import DEFAULT_PROJECT_ROOT, load_documents
from .indexing.rag import DocumentRag, RetrievalSettings
from .indexing.rerank import LocalCrossEncoderReranker
from .indexing.store import DEFAULT_INDEX_DIR, DocumentIndex, DocumentIndexError
from .invariants import (
    InvariantSnapshot, InvariantUpdateRequest, InvariantSettingsConflict,
    SQLiteInvariantRepository,
)
from .memory.updates import MemoryUpdateConflict
from .state.task import TaskConflict
from .providers.errors import (
    LlmConfigurationError,
    LlmContextLimitError,
    LlmRequestError,
)
from .providers.registry import ModelRegistry
from .project_help.mcp_client import ProjectMcpClient
from .project_help.service import ProjectHelpService
from .private_service import PrivateServiceMiddleware, ServiceLimits
from .schemas import (
    ChatSendRequest,
    ChatSendResponse,
    ChatOperationStatus,
    ChatSession,
    ChatSessionCreateRequest,
    ChatSessionSummary,
    ChatModelUpdateRequest,
    MemoryCreateRequest,
    MemoryEntry,
    MemorySnapshot,
    TaskActionRequest, TaskStartRequest,
)
from .services.chat_sessions import (
    ChatSessionService,
)
from .storage.chat_sessions import (
    ChatSessionNotFound, ChatTurnConflict,
    DEFAULT_CHAT_DB_PATH,
    SQLiteChatSessionRepository,
)
from .memory.service import (
    MemoryNotFound,
    MemoryService,
    MemoryValidationError,
    SQLiteMemoryRepository,
)


def _allowed_origins() -> list[str]:
    configured_origins = os.getenv("FRONTEND_ORIGINS")
    if not configured_origins:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    return [origin.strip() for origin in configured_origins.split(",") if origin.strip()]


app = FastAPI(title="AI Challenge W6 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["DELETE", "GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)
app.add_middleware(PrivateServiceMiddleware)


def _model_failure(error: Exception) -> HTTPException:
    detail = str(error)
    if getattr(error, "branch_session_id", None):
        detail = {"message": detail, "session_id": error.branch_session_id,
                  "message_id": error.branch_message_id}
    return HTTPException(status_code=422 if isinstance(error, LlmContextLimitError) else 502,
                         detail=detail)


@app.exception_handler(LlmConfigurationError)
async def model_configuration_error(request: Request, error: LlmConfigurationError):
    return JSONResponse({"detail": str(error)}, status_code=503)


@lru_cache(maxsize=1)
def get_chat_repository() -> SQLiteChatSessionRepository:
    database_path = os.getenv("CHAT_DB_PATH") or str(DEFAULT_CHAT_DB_PATH)
    return SQLiteChatSessionRepository(database_path)


@lru_cache(maxsize=1)
def get_memory_repository() -> SQLiteMemoryRepository:
    database_path = os.getenv("CHAT_DB_PATH") or str(DEFAULT_CHAT_DB_PATH)
    get_chat_repository()
    return SQLiteMemoryRepository(database_path)


def get_chat_session_service() -> ChatSessionService:
    return ChatSessionService(
        get_chat_repository(),
        memory_repository=get_memory_repository(),
        invariant_repository=get_invariant_repository(),
        model_registry=get_model_registry(),
        project_help=get_project_help_service,
    )


def get_model_registry() -> ModelRegistry:
    return ModelRegistry()


@lru_cache(maxsize=1)
def get_invariant_repository() -> SQLiteInvariantRepository:
    database_path = os.getenv("CHAT_DB_PATH") or str(DEFAULT_CHAT_DB_PATH)
    return SQLiteInvariantRepository(database_path)


@app.get("/api/invariants", response_model=InvariantSnapshot)
def get_invariants(
    repository: SQLiteInvariantRepository = Depends(get_invariant_repository),
) -> InvariantSnapshot:
    return repository.get()


@app.put("/api/invariants", response_model=InvariantSnapshot)
def update_invariants(
    request: InvariantUpdateRequest,
    repository: SQLiteInvariantRepository = Depends(get_invariant_repository),
) -> InvariantSnapshot:
    try:
        return repository.update(request)
    except InvariantSettingsConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


def get_memory_service() -> MemoryService:
    return MemoryService(
        get_memory_repository(),
        get_chat_repository(),
    )


def get_goal_coordinator() -> Callable[[], ChatSessionService]:
    """Only goal routing needs a chat runtime; other memory writes stay independent."""
    return get_chat_session_service


@app.get("/api/health")
def health() -> dict[str, bool | str]:
    return {
        "status": "ok",
        "deepseek_configured": bool(os.getenv("DEEPSEEK_API_KEY")),
        "network_mode": os.getenv("RAG_NETWORK_MODE", "normal"),
    }


@app.get("/api/models")
def model_catalog(registry: ModelRegistry = Depends(get_model_registry)) -> dict:
    return registry.catalog()


@app.get("/api/service")
def service_limits() -> dict:
    limits = ServiceLimits.from_env()
    return {"private": limits.enabled, "limits": {
        "max_context": limits.max_context, "max_output": limits.max_output,
        "generations_per_minute": limits.generations_per_minute,
        "active_generations": 1, "max_queue": limits.max_queue,
        "queue_wait_seconds": limits.queue_wait_seconds, "max_body_bytes": limits.max_body_bytes,
    } if limits.enabled else None}


@app.post("/api/chat/sessions", response_model=ChatSession, status_code=201)
def create_chat_session(request: ChatSessionCreateRequest | None = None,
                        service: ChatSessionService = Depends(get_chat_session_service)) -> ChatSession:
    return service.create(request.provider if request else None)


@app.get("/api/chat/sessions", response_model=list[ChatSessionSummary])
def list_chat_sessions(service: ChatSessionService = Depends(get_chat_session_service)) -> list[ChatSessionSummary]:
    return service.list()


@app.delete("/api/chat/sessions", status_code=204)
def clear_chat_sessions(service: ChatSessionService = Depends(get_chat_session_service)) -> Response:
    service.clear()
    return Response(status_code=204)


@app.get("/api/memory", response_model=MemorySnapshot)
def get_memory(
    session_id: str | None = None,
    service: MemoryService = Depends(get_memory_service),
) -> MemorySnapshot:
    try:
        return service.snapshot(session_id)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Чат не найден") from None
    except MemoryValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@app.post("/api/memory", response_model=MemoryEntry, status_code=201)
def create_memory(
    request: MemoryCreateRequest,
    service: MemoryService = Depends(get_memory_service),
    chats: Callable[[], ChatSessionService] = Depends(get_goal_coordinator),
) -> MemoryEntry:
    try:
        if request.layer == "working" and request.category == "goal":
            return chats().set_goal(request)
        return service.create(request)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Чат не найден") from None
    except MemoryValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except (ChatTurnConflict, MemoryUpdateConflict) as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@app.delete("/api/memory/{layer}/{memory_id}", status_code=204)
def delete_memory(
    layer: Literal["working", "long_term"],
    memory_id: str,
    service: MemoryService = Depends(get_memory_service),
) -> Response:
    try:
        service.delete(layer, memory_id)
    except MemoryNotFound:
        raise HTTPException(status_code=404, detail="Запись памяти не найдена") from None
    return Response(status_code=204)


@app.get("/api/chat/sessions/{session_id}", response_model=ChatSession)
def get_chat_session(
    session_id: str,
    service: ChatSessionService = Depends(get_chat_session_service),
) -> ChatSession:
    try:
        return service.get(session_id)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Чат не найден") from None


@app.put("/api/chat/sessions/{session_id}/model", response_model=ChatSession)
def update_chat_model(
    session_id: str, request: ChatModelUpdateRequest,
    service: ChatSessionService = Depends(get_chat_session_service),
) -> ChatSession:
    try:
        return service.set_provider(session_id, request.provider)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Чат не найден") from None
    except LlmConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.get("/api/chat/sessions/{session_id}/operations/{operation_id}", response_model=ChatOperationStatus)
def chat_operation_status(session_id: str, operation_id: str,
                          service: ChatSessionService = Depends(get_chat_session_service)) -> ChatOperationStatus:
    try:
        return service.operation_status(session_id, operation_id)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Чат не найден") from None
    except ChatTurnConflict as error:
        raise HTTPException(status_code=404, detail=str(error)) from None


@app.post(
    "/api/chat/sessions/{session_id}/messages",
    response_model=ChatSendResponse,
)
def send_chat_message(
    session_id: str,
    request: ChatSendRequest,
    service: ChatSessionService = Depends(get_chat_session_service),
) -> ChatSendResponse:
    try:
        return service.send(session_id, request.content, request.provider, operation_id=request.operation_id)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Чат не найден") from None
    except (ChatTurnConflict, MemoryUpdateConflict) as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except TaskConflict as error:
        raise HTTPException(status_code=409, detail=error.detail) from None
    except AgentInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except (AgentOutputError, LlmRequestError) as error:
        raise _model_failure(error) from None
    except LlmConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.post("/api/chat/sessions/{session_id}/messages/{message_id}/retry", response_model=ChatSendResponse)
def retry_chat_message(
    session_id: str, message_id: str,
    service: ChatSessionService = Depends(get_chat_session_service),
) -> ChatSendResponse:
    try:
        return service.retry(session_id, message_id)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Чат не найден") from None
    except (ChatTurnConflict, MemoryUpdateConflict) as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except TaskConflict as error:
        raise HTTPException(status_code=409, detail=error.detail) from None
    except (AgentOutputError, LlmRequestError) as error:
        raise _model_failure(error) from None
    except LlmConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except AgentInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@app.post("/api/chat/sessions/{session_id}/task", response_model=ChatSession, status_code=201)
def start_task(
    session_id: str,
    request: TaskStartRequest,
    response: Response,
    service: ChatSessionService = Depends(get_chat_session_service),
) -> ChatSession:
    try:
        session = service.start_task(session_id, request.task, operation_id=request.operation_id)
        if session.task is None:
            response.status_code = 200
        return session
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Чат не найден") from None
    except ChatTurnConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except TaskConflict as error:
        raise HTTPException(status_code=409, detail=error.detail) from None


@app.post("/api/chat/sessions/{session_id}/task/actions", response_model=ChatSession)
def task_action(
    session_id: str,
    request: TaskActionRequest,
    service: ChatSessionService = Depends(get_chat_session_service),
) -> ChatSession:
    try:
        return service.task_action(session_id, request)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Чат не найден") from None
    except TaskConflict as error:
        raise HTTPException(status_code=409, detail=error.detail) from None
    except (AgentOutputError, LlmRequestError) as error:
        raise _model_failure(error) from None
    except LlmConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@lru_cache(maxsize=1)
def get_document_index() -> DocumentIndex:
    return DocumentIndex(
        root=Path(os.getenv("DOCUMENT_INDEX_DIR", str(DEFAULT_INDEX_DIR))),
        project_root=Path(os.getenv("PROJECT_ROOT", str(DEFAULT_PROJECT_ROOT))),
    )


@lru_cache(maxsize=1)
def get_document_reranker() -> LocalCrossEncoderReranker:
    return LocalCrossEncoderReranker()


@lru_cache(maxsize=1)
def get_project_help_service() -> ProjectHelpService:
    index = get_document_index()
    model = get_model_registry().build(get_model_registry().resolve("ollama"))
    return ProjectHelpService(
        DocumentRag(index, model, reranker=get_document_reranker(),
                    settings=RetrievalSettings(rewrite=False, final_k=3)),
        ProjectMcpClient(index.project_root),
    )


@app.exception_handler(DocumentIndexError)
async def document_index_error(request: Request, error: DocumentIndexError):
    return JSONResponse({"detail": str(error)}, status_code=503)


@app.get("/api/document-index/status")
def document_index_status() -> dict:
    try:
        return get_document_index().status()
    except DocumentIndexError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.get("/api/document-index/source", response_class=HTMLResponse)
def document_source(path: str, sha256: str | None = None) -> HTMLResponse:
    # Only members of the corpus can be opened; arbitrary filesystem paths are rejected.
    try:
        document = next((doc for doc in load_documents(get_document_index().project_root)
                         if doc.path == path), None)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Документация недоступна") from error
    if document is None:
        raise HTTPException(status_code=404, detail="Документ не найден в корпусе проекта")
    if sha256 is not None and sha256 != document.sha256:
        raise HTTPException(status_code=409, detail="Документ изменился после ответа; задайте вопрос заново")
    lines = "\n".join(
        f'<span id="L{number}"><a href="#L{number}">{number:4}</a> {escape(line)}</span>'
        for number, line in enumerate(document.text.splitlines(), 1)
    )
    return HTMLResponse(
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(path)}</title><style>body{{margin:24px;background:#111827;color:#e5e7eb}}'
        'pre{white-space:pre-wrap;line-height:1.6}span{display:block;scroll-margin:24px}'
        'span:target{background:#334155}a{color:#94a3b8;text-decoration:none}</style>'
        f'<h1>{escape(path)}</h1><pre>{lines}</pre></html>',
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
    )


STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"
app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
