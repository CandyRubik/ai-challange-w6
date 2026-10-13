from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import subprocess

from .models import Snapshot

MAX_BYTES = 512_000


class GitError(ValueError):
    pass


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    before_path: str
    before_mode: str
    after_mode: str


class Repository:
    def __init__(self, root: Path):
        self.root = root

    def run(self, *args: str) -> bytes:
        try:
            return subprocess.run(
                ["git", "--no-optional-locks", "--literal-pathspecs", "-C", str(self.root), *args],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
            ).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
            raise GitError(f"Git operation failed: {args[0]}") from error

    def paths(self, sha: str) -> list[str]:
        return [path.decode("utf-8") for path in self.run("ls-tree", "-r", "-z", "--name-only", sha).split(b"\0") if path]

    def snapshot(self, base: str, head: str) -> tuple[Snapshot, list[Change]]:
        base_sha, head_sha = (
            self.run("rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
            for ref in (base, head)
        )
        ancestor = self.run("merge-base", base_sha, head_sha).decode().strip()
        raw = self.run("diff", "--raw", "--no-abbrev", "-z", "--find-renames",
                       "--no-ext-diff", "--no-textconv", ancestor, head_sha, "--")
        fields, changes = iter(raw.rstrip(b"\0").split(b"\0")), []
        if raw:
            for header in fields:
                metadata = header.decode().split()
                status = metadata[-1]
                before = next(fields).decode("utf-8")
                after = next(fields).decode("utf-8") if status.startswith("R") else before
                changes.append(Change(status[0], after, before, metadata[0][1:], metadata[1]))
        return Snapshot(base_sha=base_sha, head_sha=head_sha, merge_base_sha=ancestor,
                        diff_sha256=hashlib.sha256(raw).hexdigest()), changes

    def read(self, sha: str, path: str) -> str:
        if path.startswith("/") or ".." in PurePosixPath(path).parts or not path:
            raise GitError("Source path must be repository-relative")
        entry = self.run("ls-tree", "-z", sha, "--", path).split(b"\0")[0]
        if not entry:
            raise GitError(f"Source not found: {path}")
        mode, kind, oid = entry.split(b"\t", 1)[0].split()
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise GitError(f"Unsupported source type: {path}")
        if int(self.run("cat-file", "-s", oid.decode())) > MAX_BYTES:
            raise GitError(f"Source exceeds {MAX_BYTES} bytes: {path}")
        try:
            text = self.run("cat-file", "blob", oid.decode()).decode("utf-8")
            if "\0" in text:
                raise ValueError("binary")
            return text
        except (UnicodeError, ValueError) as error:
            raise GitError(f"Source is not UTF-8 text: {path}") from error
