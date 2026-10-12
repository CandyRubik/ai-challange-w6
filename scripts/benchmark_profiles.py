"""Frozen parameter sets for independent grounded-generation benchmarks."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RagGenerationProfile:
    name: str
    num_ctx: int
    max_tokens: int
    temperature: float
    prompt_version: str
    max_num_ctx: int = 32768


PROFILES = {
    "baseline": RagGenerationProfile("baseline", 32768, 3000, 0, "baseline"),
    "compact": RagGenerationProfile("compact", 8192, 1000, 0, "baseline"),
    "optimized": RagGenerationProfile("optimized", 8192, 1000, 0, "complete-v3"),
}

