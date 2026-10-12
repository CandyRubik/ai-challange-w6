"""Real HTTP + local LLM + E5/FAISS/reranker + MCP demonstration."""

import argparse
import json
from pathlib import Path
from time import monotonic

import httpx

ROOT = Path(__file__).resolve().parents[1]


def run(base_url: str, output: Path):
    report = {"base_url": base_url, "real_llm": True, "cases": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=base_url, timeout=300, trust_env=False) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        session = request("POST", "/api/chat/sessions", json={"provider": "ollama"})
        path = f"/api/chat/sessions/{session['id']}"
        report["session_id"] = session["id"]
        before = request("GET", "/api/memory", params={"session_id": session["id"]})
        for question, expected in [
            ("/help Какая сейчас ветка?", "tool_answer"),
            ("/help", "answered"),
            ("/help Где хранятся чат-сессии и память?", "answered"),
            ("/help Как настроен Apache Spark в проекте?", "insufficient_context"),
        ]:
            started = monotonic()
            answer = request("POST", path + "/messages", json={"content": question})["assistant_message"]
            details = answer["project_help"]
            report["cases"].append({"question": question, "elapsed_seconds": monotonic() - started, "answer": answer})
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(question, details["grounding_status"], round(report["cases"][-1]["elapsed_seconds"], 2), flush=True)
            assert details["mcp"]["status"] == "ok", details["mcp"]
            assert details["grounding_status"] == expected, answer
            if expected == "answered":
                source = next(source for source in details["sources"] if source["kind"] == "document")
                response = client.get("/api/document-index/source", params={"path": source["source"], "sha256": source["sha256"]})
                response.raise_for_status()
                assert details["citations"] and f'id="L{source["line_start"]}"' in response.text
        assert request("GET", "/api/memory", params={"session_id": session["id"]}) == before
        restored = request("GET", path)
        assert len(restored["messages"]) == len(report["cases"]) * 2
        assert restored["messages"][-1]["project_help"] == report["cases"][-1]["answer"]["project_help"]
        report["complete"] = True
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("PROJECT_HELP_SMOKE_COMPLETE", session["id"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=ROOT / "data/day31-smoke.json")
    args = parser.parse_args()
    run(args.base_url, args.output)
