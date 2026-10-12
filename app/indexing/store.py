from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Condition
from typing import Any, Protocol
from uuid import uuid4

from .chunking import fixed_chunks, structured_chunks
from .corpus import DEFAULT_PROJECT_ROOT, TITLE, load_documents, corpus_fingerprint


MODEL_NAME = "intfloat/multilingual-e5-small"
DEFAULT_INDEX_DIR = Path(__file__).resolve().parents[2] / "data/project_index"
STRATEGIES = ("fixed", "structure")


class Embedder(Protocol):
    tokenizer: Any
    def encode(self, sentences: list[str], *, normalize_embeddings: bool, batch_size: int) -> Any: ...


@dataclass(frozen=True)
class SearchHit:
    metadata: dict
    score: float


class DocumentIndexError(RuntimeError):
    """The local corpus could not be indexed for a requested search."""


class DocumentIndex:
    def __init__(self, root: Path = DEFAULT_INDEX_DIR, project_root: Path = DEFAULT_PROJECT_ROOT,
                 embedder: Embedder | None = None) -> None:
        self.root = Path(root)
        self.project_root = Path(project_root).resolve()
        self._embedder = embedder
        self.model_name = MODEL_NAME
        local_model = Path(__file__).resolve().parents[2] / "data/models/e5"
        self.model_path = os.getenv("DOCUMENT_EMBEDDING_MODEL", str(local_model) if local_model.is_dir() else MODEL_NAME)
        self._condition = Condition()
        self._building = False
        self._error: str | None = None

    def _model(self) -> Embedder:
        if self._embedder is None:
            try:
                import torch
                from sentence_transformers import SentenceTransformer
            except ImportError as error:
                raise DocumentIndexError("Установите зависимости: pip install -r requirements-indexing.txt") from error
            torch.set_num_threads(1)
            try:
                self._embedder = SentenceTransformer(
                    self.model_path, device="cpu", local_files_only=True,
                )
            except (OSError, ValueError) as error:
                raise DocumentIndexError(
                    "Локальные веса E5 не найдены. Выполните scripts/prepare_local_rag.py"
                ) from error
        return self._embedder

    def _active(self) -> dict | None:
        path = self.root / "active.json"
        if not path.is_file():
            return None
        try:
            active = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DocumentIndexError("Не удалось прочитать манифест локального индекса") from error
        if not isinstance(active, dict) or not isinstance(active.get("version"), str):
            raise DocumentIndexError("Некорректный манифест локального индекса")
        if active.get("corpus") != "project-docs-v1":
            return None
        if active.get("model") != self.model_name:
            raise DocumentIndexError("Индекс построен другой моделью эмбеддингов")
        directory = (self.root / active["version"]).resolve()
        if not directory.is_relative_to(self.root.resolve()) or any(
            not (directory / f"{strategy}.{extension}").is_file()
            for strategy in STRATEGIES for extension in ("faiss", "json")
        ):
            raise DocumentIndexError("Файлы локального индекса отсутствуют или повреждены")
        return active

    def status(self) -> dict:
        active = self._active()
        with self._condition:
            building, error = self._building, self._error
        return {
            "state": "building" if building else "error" if error else "ready" if active else "missing",
            "error": error, "documents_present": (self.project_root / "README.md").is_file(),
            "source": str(self.project_root), "model": self.model_name,
            "index": active,
            "stale": bool(active and active.get("fingerprint") != self._fingerprint()),
        }

    def _fingerprint(self) -> str:
        try:
            return corpus_fingerprint(load_documents(self.project_root))
        except (OSError, ValueError) as error:
            raise DocumentIndexError("Не удалось прочитать README и docs проекта") from error

    def ensure_built(self) -> dict:
        """Build once on the first retrieval request; concurrent callers share the result."""
        with self._condition:
            active = self._active()
            if active is not None and active.get("fingerprint") == self._fingerprint():
                return active
            if self._building:
                self._condition.wait_for(lambda: not self._building)
                active = self._active()
                if active is not None and active.get("fingerprint") == self._fingerprint():
                    return active
                raise DocumentIndexError(self._error or "Не удалось построить индекс")
            self._building = True
            self._error = None
        try:
            manifest = self.build()
        except Exception as error:
            with self._condition:
                self._error = f"{type(error).__name__}: {error}"
                self._building = False
                self._condition.notify_all()
            raise DocumentIndexError(self._error) from error
        with self._condition:
            self._building = False
            self._condition.notify_all()
        return manifest

    def build(self) -> dict:
        import faiss
        import numpy as np

        documents = load_documents(self.project_root)
        model = self._model()
        tokenizer = model.tokenizer
        strategies = {
            "fixed": fixed_chunks(documents, tokenizer),
            "structure": structured_chunks(documents, tokenizer),
        }
        version = uuid4().hex
        directory = self.root / version
        directory.mkdir(parents=True, exist_ok=False)
        stats: dict[str, dict] = {}
        for strategy, chunks in strategies.items():
            if not chunks:
                raise ValueError(f"Нет фрагментов для стратегии {strategy}")
            embeddings = np.asarray(model.encode(
                ["passage: " + chunk.text for chunk in chunks],
                normalize_embeddings=True, batch_size=32,
            ), dtype="float32")
            if embeddings.ndim != 2 or len(embeddings) != len(chunks):
                raise ValueError("Модель вернула некорректные эмбеддинги")
            index = faiss.IndexFlatIP(embeddings.shape[1])
            index.add(embeddings)
            faiss.write_index(index, str(directory / f"{strategy}.faiss"))
            (directory / f"{strategy}.json").write_text(
                json.dumps([chunk.metadata() for chunk in chunks], ensure_ascii=False),
                encoding="utf-8",
            )
            sizes = [chunk.token_count for chunk in chunks]
            stats[strategy] = {
                "chunks": len(chunks), "avg_tokens": round(sum(sizes) / len(sizes), 1),
                "min_tokens": min(sizes), "max_tokens": max(sizes),
                "index_bytes": (directory / f"{strategy}.faiss").stat().st_size,
            }
        manifest = {
            "version": version, "corpus": "project-docs-v1",
            "fingerprint": corpus_fingerprint(documents),
            "files": {doc.path: doc.sha256 for doc in documents},
            "title": TITLE,
            "documents": len(documents), "model": self.model_name,
            "created_at": datetime.now(timezone.utc).isoformat(), "strategies": stats,
        }
        self.root.mkdir(parents=True, exist_ok=True)
        pointer = self.root / "active.json.tmp"
        pointer.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        pointer.replace(self.root / "active.json")
        return manifest

    def search(self, query: str, strategy: str = "structure", k: int = 3) -> list[SearchHit]:
        if strategy not in STRATEGIES:
            raise ValueError("Неизвестная стратегия чанкинга")
        active = self._active()
        if active is None:
            return []
        import faiss
        import numpy as np

        directory = self.root / active["version"]
        index = faiss.read_index(str(directory / f"{strategy}.faiss"))
        metadata = json.loads((directory / f"{strategy}.json").read_text(encoding="utf-8"))
        vector = np.asarray(self._model().encode(
            ["query: " + query], normalize_embeddings=True, batch_size=1,
        ), dtype="float32")
        scores, ids = index.search(vector, min(k, index.ntotal))
        return [SearchHit(metadata[int(i)], float(score)) for i, score in zip(ids[0], scores[0]) if i >= 0]
