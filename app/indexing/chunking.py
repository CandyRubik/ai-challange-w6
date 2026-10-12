"""Token-bounded Markdown chunks with exact source text and line locations."""

from dataclasses import dataclass, asdict
import re
from typing import Protocol

from .corpus import Document, TITLE


class Tokenizer(Protocol):
    def encode(self, text: str, add_special_tokens: bool = False, verbose: bool = False) -> list[int]: ...


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    strategy: str
    source: str
    title: str
    section: str
    line_start: int
    line_end: int
    text: str
    token_count: int
    sha256: str

    def metadata(self) -> dict:
        return asdict(self)


def _spans(document: Document, structured: bool):
    if not structured:
        yield 0, len(document.text), document.path
        return
    start, offset, section, fence = 0, 0, document.path, None
    headings: list[tuple[int, str]] = []
    for line in document.text.splitlines(keepends=True):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            character = marker[1][0]
            fence = None if fence == character else character if fence is None else fence
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line) if fence is None else None
        if heading:
            if offset > start:
                yield start, offset, section
            level = len(heading[1])
            headings = [(depth, title) for depth, title in headings if depth < level]
            headings.append((level, heading[2]))
            section = " → ".join(title for _, title in headings)
            start = offset
        offset += len(line)
    if offset > start:
        yield start, offset, section


def _chunks(documents, tokenizer, strategy, size, overlap=0):
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("Некорректные размер чанка или перекрытие")
    result = []
    def count(text):
        return len(tokenizer.encode(text, add_special_tokens=False, verbose=False))
    for document in documents:
        for start, stop, section in _spans(document, strategy == "structure"):
            while start < stop:
                low, high = start + 1, min(stop, start + 1800)
                while low < high:
                    middle = (low + high + 1) // 2
                    if count(document.text[start:middle]) <= size:
                        low = middle
                    else:
                        high = middle - 1
                end = low
                if end < stop:
                    boundary = max(document.text.rfind("\n", start, end), document.text.rfind(" ", start, end))
                    if boundary > start + (end - start) // 2:
                        end = boundary + 1
                text = document.text[start:end]
                if text.strip():
                    result.append(Chunk(
                        f"{strategy}-{len(result):04d}", strategy, document.path, TITLE, section,
                        document.text.count("\n", 0, start) + 1,
                        document.text.count("\n", 0, end - 1) + 1,
                        text, count(text), document.sha256,
                    ))
                next_start = end
                if overlap and end < stop:
                    low, high = start + 1, end
                    while low < high:
                        middle = (low + high) // 2
                        if count(document.text[middle:end]) <= overlap:
                            high = middle
                        else:
                            low = middle + 1
                    next_start = low
                start = next_start
    return result


def fixed_chunks(documents: list[Document], tokenizer: Tokenizer, size=300, overlap=50) -> list[Chunk]:
    return _chunks(documents, tokenizer, "fixed", size, overlap)


def structured_chunks(documents: list[Document], tokenizer: Tokenizer, size=300) -> list[Chunk]:
    return _chunks(documents, tokenizer, "structure", size)
