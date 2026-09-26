import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.query.plan import PlannerOutput
from app.query.planner import build_planner
from app.utils.error import PlannerError
from evals.planner_cases import CASES, REFERENCE_DATE

RESULTS_DIR = Path(__file__).parent / "results"


def _subset_mismatches(expected: Any, actual: Any, path: str = "") -> list[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path or 'plan'}: expected an object, got {actual!r}"]
        return [m for k, v in expected.items() for m in _subset_mismatches(v, actual.get(k), f"{path}.{k}".lstrip("."))]
    if isinstance(expected, list):
        return [] if sorted(expected) == sorted(actual or []) else [f"{path}: expected {expected}, got {actual}"]
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


def grade(case: dict, output: PlannerOutput) -> list[str]:
    if case["expect"] == "unsupported":
        return [] if output.unsupported_reason else ["expected a refusal"]
    if output.unsupported_reason:
        return [f"unexpected refusal: {output.unsupported_reason}"]
    return _subset_mismatches(case["expect"], output.model_dump(mode="json"))


async def main() -> None:
    planner = build_planner()
    if planner is None:
        raise SystemExit("No planner configured: set LLM_PROVIDER / LLM_MODEL / LLM_API_KEY (see .env.example).")

    results = []
    for case in CASES:
        started = time.monotonic()
        try:
            output = await planner.plan(case["question"], REFERENCE_DATE)
            problems = grade(case, output)
            raw = output.model_dump(mode="json")
        except PlannerError as ex:  # a failed call counts as a failed case
            problems, raw = [f"{type(ex).__name__}: {ex}"], None
        latency = time.monotonic() - started
        results.append({"id": case["id"], "passed": not problems, "problems": problems, "latency_s": round(latency, 2), "output": raw})
        print(f"{'PASS' if not problems else 'FAIL'}  {case['id']:<20} {latency:5.1f}s  {'; '.join(problems)}")

    passed = sum(r["passed"] for r in results)
    latencies = sorted(r["latency_s"] for r in results)
    print(f"\n{settings.LLM_PROVIDER} / {planner.model}: {passed}/{len(results)} passed, "
          f"median latency {latencies[len(latencies) // 2]:.1f}s, max {latencies[-1]:.1f}s")

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    out = RESULTS_DIR / f"{settings.LLM_PROVIDER}-{planner.model.replace('/', '_')}-{stamp}.json"
    out.write_text(json.dumps({"provider": settings.LLM_PROVIDER, "model": planner.model, "results": results}, indent=2))
    print(f"results: {out}")


if __name__ == "__main__":
    asyncio.run(main())
