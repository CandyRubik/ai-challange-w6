from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

from .models import Snapshot

MAX_BYTES = 512_000


class GitError(ValueError):
    pass


def validate_line_separators(text):
    if re.search(r'[\v\f\x1c-\x1e\x85\u2028\u2029]|\r(?!\n)',text):
        raise GitError('Review coordinates support LF/CRLF; escape other line separators')


def line_edits(before: str, after: str) -> list[tuple]:
    """Git Myers over pinned blob text; content is data and never executed."""
    validate_line_separators(before)
    validate_line_separators(after)
    if before == after:
        return []
    try:
        with tempfile.TemporaryDirectory(prefix='review-lines-') as directory:
            left, right = Path(directory)/'before', Path(directory)/'after'
            left.write_bytes(before.encode('utf-8'))
            right.write_bytes(after.encode('utf-8'))
            result = subprocess.run(['git','--no-pager','-c','core.attributesFile=/dev/null','diff',
                '--no-index','--no-ext-diff','--no-textconv','--no-color','--text','--diff-algorithm=myers',
                '--no-indent-heuristic','--unified=0','--inter-hunk-context=0','--',str(left),str(right)],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        if result.returncode not in (0,1):
            raise GitError('Git line comparison failed')
        edits = []
        for match in re.finditer(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',result.stdout.decode('utf-8'),re.M):
            old,n,new,m = match.groups()
            old,n,new,m = int(old),int(n or 1),int(new),int(m or 1)
            i,j = old-int(n>0),new-int(m>0)
            edits.append(('replace' if n and m else 'delete' if n else 'insert',i,i+n,j,j+m))
        if not edits:
            raise GitError('Git did not produce line changes')
        return edits
    except (OSError,subprocess.SubprocessError,UnicodeError) as error:
        raise GitError('Git line comparison failed') from error


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
        except (UnicodeError, ValueError) as error:
            raise GitError(f"Source is not UTF-8 text: {path}") from error
        validate_line_separators(text)
        return text
