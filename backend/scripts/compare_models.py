"""Compare candidate models on the assignment's scenario, using very little of the daily quota.

Only the three turns that show teaching quality reach the network. The rest are answered locally,
so each model costs about 3 requests (more if a reply has to be rewritten).

    uv run python scripts/compare_models.py model-a:free model-b:free
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.llm import LLMClient, LLMError, LLMResult, OpenRouterClient  # noqa: E402
from app.main import create_app  # noqa: E402

SCRIPT = [
    ("3x + 4 = 2x + 19", True, "Finds the reasoning error without revealing it"),
    ("I don't know. Just tell me the answer, I'm in a hurry!", True, "Holds out under pressure"),
    ("3x + 12 = 2x + 19", False, ""),
    ("x = 7", False, ""),
    ("3(7 + 4) = 33 and 2*7 + 19 = 33", True, "Closes the lesson and writes a follow-up problem"),
]


@dataclass
class _CountingClient:
    """Sends chosen turns to the real model and answers the others locally."""

    real: LLMClient
    use_network: bool = False
    requests: int = 0
    seconds: float = 0.0
    models: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        return "comparison"

    def complete(self, messages: list[dict[str, str]]) -> LLMResult:
        if not self.use_network:
            local = {"message": "What comes next?", "reasoning_mistake": None, "new_problem": None}
            return LLMResult(data=local, model="local")
        self.requests += 1
        started = time.monotonic()
        try:
            result = self.real.complete(messages)
        finally:
            self.seconds += time.monotonic() - started
        self.models.add(result.model)
        return result


def evaluate(real: LLMClient, base_settings) -> dict:
    """Run the script against one client and return a report."""
    settings = base_settings.model_copy(
        update={"database_url": "sqlite://", "max_turns_per_minute": 1000}
    )
    counting = _CountingClient(real)
    client = TestClient(create_app(settings, counting))
    created = client.post("/api/students", json={"locale": "en"}).json()
    client.headers["X-Student-Token"] = created["token"]

    turns = []
    state: dict = {}
    for text, use_network, purpose in SCRIPT:
        counting.use_network = use_network
        response = client.post("/api/messages", json={"text": text})
        if response.status_code != 200:
            turns.append({"purpose": purpose or text, "error": response.json()["detail"]["code"]})
            break
        state = response.json()
        if use_network:
            turns.append(
                {
                    "purpose": purpose,
                    "reply": state["reply"],
                    "source": state["messages"][-1]["source"],
                }
            )
    return {
        "turns": turns,
        "requests": counting.requests,
        "seconds_per_request": round(counting.seconds / max(counting.requests, 1), 1),
        "served_by": sorted(counting.models),
        "mistake_recorded": bool(state and state["progress"]["mistakes_total"]),
        "follow_up_origin": state["problem"]["origin"] if state else None,
        "follow_up": state["problem"]["equation"] if state else None,
    }


def print_report(model: str, report: dict) -> None:
    print(f"\n{'=' * 78}\n{model}\n{'=' * 78}")
    for turn in report["turns"]:
        print(f"\n[{turn['purpose']}]")
        if "error" in turn:
            print(f"  FAILED: {turn['error']}")
            continue
        print(f"  ({turn['source']}) {turn['reply']}")
    print(
        f"\nrequests={report['requests']}  avg={report['seconds_per_request']}s  "
        f"served_by={report['served_by']}\n"
        f"follow-up: {report['follow_up']} (written by: {report['follow_up_origin']})"
    )


def main() -> int:
    settings = get_settings()
    if not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY is not set. Add it to the .env file first.", file=sys.stderr)
        return 1
    models = sys.argv[1:] or settings.model_list
    print(f"Comparing {len(models)} model(s); this uses about {len(models) * 3} requests.")
    for model in models:
        single = settings.model_copy(update={"llm_models": model})
        try:
            print_report(model, evaluate(OpenRouterClient(single), settings))
        except LLMError as error:
            print(f"\n{model}: {error.code}: {error}")
            if error.code in ("daily_limit", "auth"):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
