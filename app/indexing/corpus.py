"""The only RAG corpus: this checkout's README and Markdown documentation."""

from dataclasses import dataclass
import hashlib
from pathlib import Path

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
TITLE = "AI Challenge W6 — документация проекта"


@dataclass(frozen=True)
class Document:
    path: str
    text: str
    sha256: str


def document_paths(root: Path) -> list[Path]:
    root = root.resolve()
    paths = [root / "README.md"]
    docs = root / "docs"
    if docs.is_dir() and not docs.is_symlink():
        paths.extend(sorted(docs.rglob("*.md")))
    return [path for path in paths if path.is_file() and not path.is_symlink()
            and path.resolve().is_relative_to(root)]


def load_documents(root: Path = DEFAULT_PROJECT_ROOT) -> list[Document]:
    root = root.resolve()
    documents = []
    for path in document_paths(root):
        raw = path.read_bytes()
        if len(raw) > 512_000:
            raise ValueError(f"Документ слишком большой: {path.relative_to(root)}")
        documents.append(Document(path.relative_to(root).as_posix(), raw.decode("utf-8"),
                                  hashlib.sha256(raw).hexdigest()))
    if not any(document.path == "README.md" for document in documents):
        raise ValueError("README.md проекта не найден")
    return documents


def corpus_fingerprint(documents: list[Document]) -> str:
    contents = "\n".join(f"{doc.path}:{doc.sha256}" for doc in documents)
    return hashlib.sha256(contents.encode()).hexdigest()
