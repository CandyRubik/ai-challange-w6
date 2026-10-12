"""Check shared document retrieval and cold/warm generation after the isolated benchmark."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from time import monotonic

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import main
from app.agents.agent import Agent
from app.indexing.store import SearchHit
from app.providers.ollama import OllamaProvider
from app.indexing.generation import GROUNDING_SYSTEM_PROMPT, generate_grounded_answer
from scripts.benchmark_profiles import PROFILES
from scripts.run_day29_evaluation import power_state


def main_check():
    os.environ.update(DEEPSEEK_API_KEY="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", LLM_DEFAULT_PROVIDER="ollama",
                      OLLAMA_MODEL="qwen3.5:9b-q4_K_M", OLLAMA_NUM_CTX="32768")
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    cases = json.loads((ROOT / "evaluation/day29-cases.json").read_text())["cases"]
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "cold_warm": [],
              "power_state_start": power_state(),
              "method": "Direct Qwen grounded generation and local E5/FAISS/reranker startup; cloud key absent. Cold/warm observations use identical frozen evidence.",
              "limitations": "Small descriptive timing sample; unloading weights leaves operating-system disk cache intact. External sockets are not blocked in this check."}
    target = ROOT / "data/day29-application-check.json"
    def save():
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with httpx.Client(timeout=30, trust_env=False) as control:
        for name in ("baseline", "optimized"):
            profile = PROFILES[name]
            for loaded in control.get(base + "/api/ps").json()["models"]:
                control.post(base + "/api/generate", json={"model": loaded["name"], "keep_alive": 0}).raise_for_status()
            model = OllamaProvider(model="qwen3.5:9b-q4_K_M", base_url=base, num_ctx=profile.num_ctx,
                                   max_num_ctx=profile.max_num_ctx, temperature=profile.temperature)
            case = cases[2]
            for label in ("cold", "warm"):
                model.request_metrics.clear()
                started = monotonic()
                result = generate_grounded_answer(Agent(model, system_prompt=GROUNDING_SYSTEM_PROMPT, max_tokens=profile.max_tokens),
                                                  case["question"], case["search_question"],
                                                  [SearchHit(h, h.get("score", 1)) for h in case["hits"]],
                                                  prompt_version=profile.prompt_version)
                report["cold_warm"].append({"profile": name, "label": label, "question_id": case["id"],
                                             "question": case["question"], "elapsed_seconds": monotonic() - started,
                                             "requests": model.request_metrics[:], **asdict(result)})
                assert result.status == "answered"
                save()
        started = monotonic()
        main.get_document_index().ensure_built()
        hits = main.get_document_index().search("ChatSessionService", k=3)
        main.get_document_reranker().score("ChatSessionService", hits)
        report["retrieval_startup_seconds"] = monotonic() - started
    report["complete"] = True
    report["power_state_end"] = power_state()
    save()
    print(target)


if __name__ == "__main__":
    main_check()
