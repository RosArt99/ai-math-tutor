"""The comparison script must spend few requests and report what happened."""

import sys
from pathlib import Path

from test_tutor import ScriptedLLM

from app.config import Settings
from app.llm import LLMError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from compare_models import evaluate  # noqa: E402


def settings() -> Settings:
    return Settings(_env_file=None)


def test_uses_three_requests_and_reports_the_follow_up():
    llm = ScriptedLLM(
        "What does the 3 multiply?",
        "What does the 3 multiply?",
        {
            "message": "The check works. Try another.",
            "reasoning_mistake": None,
            "new_problem": {"equation": "4(x + 2) = 3x + 11"},
        },
    )
    report = evaluate(llm, settings())
    assert report["requests"] == 3
    assert report["mistake_recorded"] is True
    assert report["follow_up"] == "4(x + 2) = 3x + 11"
    assert report["follow_up_origin"] == "model"
    assert [turn["source"] for turn in report["turns"]] == ["model"] * 3


def test_stops_and_reports_when_the_model_fails():
    report = evaluate(ScriptedLLM(LLMError("unavailable")), settings())
    assert report["turns"][0]["error"] == "llm_unavailable"
    assert report["requests"] == 1
