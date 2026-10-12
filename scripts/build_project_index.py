"""Build/rebuild the local README + docs RAG index."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.indexing.store import DocumentIndex, DEFAULT_INDEX_DIR

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    args = parser.parse_args()
    result = DocumentIndex(root=args.index_dir, project_root=args.project_root).build()
    print(json.dumps(result, ensure_ascii=False, indent=2))
