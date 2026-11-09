"""The chapters beyond linear equations: fractions, and expanding and simplifying."""

import random

import pytest
from test_tutor import say, session  # noqa: F401  (fixture and helper)

from app import curriculum
from app import math_engine as me


def eq(text):
    return me.parse_equation(text)


class TestFractionDiagnosis:
    @pytest.mark.parametrize(
        ("previous", "student", "expected"),
        [
            ("x/2 + x/3 = 5", "3x + 2x = 5", "lcd_not_all_terms"),
            ("x/4 - 2 = x/6", "3x - 2 = 2x", "lcd_not_all_terms"),
            ("(x + 1)/3 = (x - 1)/2", "2x + 1 = 3x - 3", "lcd_numerator_partial"),
        ],
    )
    def test_recognises_clearing_errors(self, previous, student, expected):
        diagnosis = me.diagnose(eq(previous), eq(student))
        assert diagnosis.type == expected
        assert diagnosis.skill == "clearing_fractions"

    def test_correct_clearing_is_named(self):
        assert me.describe_step(eq("x/2 + x/3 = 5"), eq("3x + 2x = 30")) == "cleared_fractions"


class TestExpressionMode:
    ORIGINAL = "(x + 3)^2 - x(x - 2)"

    def steps(self, text):
        original = me.parse_problem_expression(self.ORIGINAL)
        return me.check_expression_steps(
            original, original, None, me.extract_expression_chain(text)
        )

    @pytest.mark.parametrize(
        ("written", "expected"),
        [
            ("x^2 + 9 - x^2 + 2x", "square_missing_middle"),
            ("x^2 + 3x + 9 - x^2 + 2x", "square_middle_not_doubled"),
            ("x^2 + 6x + 9 - x^2 - 2x", "distribution_sign"),
        ],
    )
    def test_recognises_classic_errors(self, written, expected):
        [step] = self.steps(written)
        assert step.status == "invalid"
        assert step.diagnosis.type == expected

    def test_a_chain_of_forms_is_followed_to_the_simplified_result(self):
        steps = self.steps(f"{self.ORIGINAL} = x^2 + 6x + 9 - x^2 + 2x = 8x + 9")
        assert [s.status for s in steps] == ["restated", "valid", "final_correct"]
        assert steps[1].kind == "expanded_square"

    def test_reveal_guard(self):
        original = me.parse_problem_expression(self.ORIGINAL)
        assert me.find_expression_reveal("So it is 8x + 9.", original, original, [])
        assert me.find_expression_reveal("(x+3)^2 is x^2 + 6x + 9", original, original, [])
        assert me.find_expression_reveal("What is (x + 3)^2?", original, original, []) is None


class TestFractionHints:
    def test_the_common_denominator_may_be_named_as_a_hint(self, session):  # noqa: F811
        client, llm = session("Multiply every term by the common denominator 6. Did you?")
        client.post("/api/topics/fractions/start")
        state = say(client, "3x + 2x = 10")
        assert state["reply"].startswith("Multiply every term")
        assert len(llm.requests) == 1


class TestCurriculum:
    @pytest.mark.parametrize("topic_id", list(curriculum.TOPICS))
    @pytest.mark.parametrize("difficulty", [1, 2, 3])
    def test_generated_problems_are_sound(self, topic_id, difficulty):
        rng = random.Random(7)
        chapter = curriculum.TOPICS[topic_id]
        for skill in chapter.skills:
            problem = curriculum.generate(topic_id, skill, difficulty, [], rng)
            assert curriculum.infer_topic(problem) == topic_id

    def test_examples_exercise_their_signature_skills(self):
        assert "distribution" in curriculum.skills_exercised("linear", "3(x + 4) = 2x + 19")
        assert "clearing_fractions" in curriculum.skills_exercised("fractions", "x/2 + x/3 = 10")
        assert {"special_products", "distribution"} <= curriculum.skills_exercised(
            "expressions", "(x + 3)^2 - x(x - 2)"
        )


class TestTopicLessons:
    def test_expression_lesson_with_follow_up(self, session):  # noqa: F811
        client, llm = session()
        state = client.post("/api/topics/expressions/start").json()
        assert state["problem"]["equation"] == "(x + 3)^2 - x(x - 2)"
        assert state["problem"]["task"] == "Expand and simplify"

        state = say(client, "x^2 + 9 - x^2 + 2x")
        assert llm.facts["diagnosis"]["type"] == "square_missing_middle"
        assert llm.facts["topic"] == "Expanding and simplifying"

        state = say(client, "x^2 + 6x + 9 - x^2 + 2x = 8x + 9")
        assert state["problem_completed"] is True
        assert llm.facts["next_problem_request"]["purpose"] == "follow_up"
        assert "no equals sign" in llm.facts["next_problem_request"]["format"]
        assert state["problem"]["topic"] == "expressions"
        assert state["problem"]["purpose"] == "follow_up"
        assert "=" not in state["problem"]["equation"]

        chapter = next(t for t in state["progress"]["topics"] if t["id"] == "expressions")
        special = next(s for s in chapter["skills"] if s["skill"] == "special_products")
        assert special["mistakes"] == 1 and special["grade"] == 2

    def test_fraction_lesson_records_the_clearing_error(self, session):  # noqa: F811
        client, llm = session()
        client.post("/api/topics/fractions/start")
        say(client, "3x + 2x = 10")
        assert llm.facts["diagnosis"]["type"] == "lcd_not_all_terms"
        state = say(client, "3x + 2x = 60")
        assert llm.facts["student_steps"][0]["kind"] == "cleared_fractions"
        assert state["problem"]["topic"] == "fractions"

    def test_record_book_lists_every_chapter_with_an_example(self, session):  # noqa: F811
        client, _ = session()
        topics = client.get("/api/state").json()["progress"]["topics"]
        assert [t["id"] for t in topics] == ["linear", "fractions", "expressions"]
        assert all(t["example_latex"] and t["skills"] for t in topics)

    def test_own_expression_goes_to_the_right_chapter(self, session):  # noqa: F811
        client, _ = session()
        state = client.post("/api/problems", json={"equation": "(x - 4)^2 - x^2"}).json()
        assert state["problem"]["topic"] == "expressions"

    def test_unknown_topic(self, session):  # noqa: F811
        client, _ = session()
        assert client.post("/api/topics/geometry/start").status_code == 404


class TestExpressionWorkingHabits:
    """Seen in manual testing: students expand parts separately, and run on past the answer."""

    ORIGINAL = "(x + 5)^2 - x(x + 4)"

    def steps(self, text, last=None):
        original = me.parse_problem_expression(self.ORIGINAL)
        current = me.parse_problem_expression(last) if last else original
        return me.check_expression_steps(original, current, None, me.extract_expression_chain(text))

    def test_expanding_each_bracket_separately_is_valid_work(self):
        steps = self.steps("x^2 + 10x + 25 = x^2 + 4x")
        assert [(s.status, s.kind) for s in steps] == [
            ("valid", "worked_on_part"),
            ("valid", "worked_on_part"),
        ]

    def test_a_wrong_part_is_diagnosed(self):
        [step] = self.steps("(x + 5)^2 = x^2 + 25")[1:]
        assert step.status == "invalid"
        assert step.diagnosis.type == "square_missing_middle"

    def test_nothing_after_the_simplified_answer_is_graded(self):
        steps = self.steps(
            "10x + 25 - 4x\n6x + 25 = 0\n6x = -25\nx = -25/6", last="x^2 + 10x + 25 - x^2 - 4x"
        )
        assert [s.status for s in steps] == ["valid", "final_correct"]

    def test_lesson_completes_instead_of_recording_errors(self, session):  # noqa: F811
        client, _ = session()
        client.post("/api/problems", json={"equation": self.ORIGINAL})
        state = say(client, "x^2 + 10x + 25 - x^2 - 4x\n6x + 25 = 0\nx = -25/6")
        assert state["problem_completed"] is True
        assert state["progress"]["mistakes_total"] == 0


def test_rate_limited_models_get_a_second_chance(monkeypatch):
    import httpx

    from app.config import Settings
    from app.llm import OpenRouterClient

    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) <= 2:
            return httpx.Response(429, json={"error": {"message": "rate-limited upstream"}})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"message": "ok"}'}}]}
        )

    settings = Settings(
        _env_file=None,
        openrouter_api_key="k",
        llm_models="a:free,b:free",
        llm_retry_pause_seconds=0,
    )
    result = OpenRouterClient(settings, transport=httpx.MockTransport(handler)).complete(
        [{"role": "user", "content": "hi"}]
    )
    assert result.data["message"] == "ok"
    assert len(calls) == 3
