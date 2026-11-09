import random

import pytest
import sympy as sp

from app import math_engine as me


def eq(text: str) -> me.Equation:
    return me.parse_equation(text)


ORIGINAL = "3(x + 4) = 2x + 19"


class TestParsing:
    @pytest.mark.parametrize(
        "typed",
        ["3(x+4)=2x+19", "3*(x+4) = 2*x + 19", "3 · (X + 4) = 2х + 19", "3×(х+4)=2Х+19"],
    )
    def test_equivalent_spellings(self, typed):
        assert eq(typed).text == ORIGINAL

    def test_preserves_the_brackets_sympy_would_expand(self):
        assert isinstance(eq(ORIGINAL).left, me.Mul)
        assert eq(ORIGINAL).text == ORIGINAL

    def test_soviet_notation_and_unicode(self):
        assert sp.expand(me.to_sympy(me.parse_expression("6:2"))) == 3
        assert sp.expand(me.to_sympy(me.parse_expression("2,5x"))) == sp.Rational(5, 2) * me.X
        assert me.to_sympy(me.parse_expression("x²")) == me.X**2
        assert me.to_sympy(me.parse_expression("5 − x")) == 5 - me.X

    @pytest.mark.parametrize(
        "bad",
        [
            "__import__('os').system('id')",
            "1/0",
            "1/x",
            "x^99",
            "9^9^9",
            "((((",
            "x2",
            "x y",
            "",
            "1" * 13,
            "(" * 25 + "x" + ")" * 25,
            "x" * 300,
        ],
    )
    def test_rejects_unsafe_or_unsupported_input(self, bad):
        with pytest.raises(me.ParseError):
            me.parse_expression(bad)

    def test_requires_exactly_one_equals(self):
        with pytest.raises(me.ParseError):
            eq("x = 1 = 2")


class TestSolutions:
    def test_scenario_roots(self):
        assert eq(ORIGINAL).solutions().values == (sp.Integer(7),)
        assert eq("3x + 4 = 2x + 19").solutions().values == (sp.Integer(15),)

    def test_identity_contradiction_and_quadratic(self):
        assert eq("x + 1 = x + 1").solutions().kind == "all"
        assert eq("x + 1 = x + 2").solutions().kind == "none"
        assert eq("x^2 = 4").solutions().values == (sp.Integer(-2), sp.Integer(2))
        assert eq("x^2 + 1 = 0").solutions().kind == "none"
        assert eq("x^3 = 8").solutions().kind == "unsupported"

    def test_solved_value(self):
        assert eq("x = 7").solved_value() == 7
        assert eq("7 = x").solved_value() == 7
        assert eq("2x = 14").solved_value() is None


class TestExtraction:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("I wrote 3x + 4 = 2x + 19", ["3x + 4 = 2x + 19"]),
            ("3x+4=2x+19, so x = 15", ["3x + 4 = 2x + 19", "x = 15"]),
            ("3x+4=2x+19 => x=15", ["3x + 4 = 2x + 19", "x = 15"]),
            ("Я написал 3х + 12 = 2х + 19, потом х = 7.", ["3x + 12 = 2x + 19", "x = 7"]),
            ("1) 3x + 12 = 2x + 19\n2) x = 7", ["3x + 12 = 2x + 19", "x = 7"]),
            ("- 3x = 12", ["3x = 12"]),
            ("well — x = 5", ["x = 5"]),
            ("$3x + 12 = 2x + 19$", ["3x + 12 = 2x + 19"]),
            (r"\(\frac{x}{2} = 3\)", ["x / 2 = 3"]),
            ("max=5 and next=6", []),
            ("I do not understand, why is that?", []),
            ("Answer: x=7", ["x = 7"]),
            ("значит $4 \\cdot 5$ равно 20", ["4 * 5 = 20"]),
            ("x равен 7", ["x = 7"]),
            ("мне всё равно", []),
        ],
    )
    def test_extract(self, message, expected):
        assert [item.text for item in me.extract_equations(message)] == expected

    def test_numeric_statements_are_kept(self):
        found = me.extract_equations("3(7+4)=33")
        assert len(found) == 1 and not found[0].has_x

    def test_message_contains_number(self):
        assert me.message_contains_number("both sides give 33", sp.Integer(33))
        assert not me.message_contains_number("both sides give 330", sp.Integer(33))
        assert me.message_contains_number("получается 2,5", sp.Rational(5, 2))

    @pytest.mark.parametrize(
        ("text", "value"), [("7", 7), (" -3. ", -3), ("x", None), ("7x", None)]
    )
    def test_bare_numbers(self, text, value):
        assert me.parse_bare_number(text) == value


class TestDiagnosis:
    @pytest.mark.parametrize(
        ("previous", "student", "expected"),
        [
            (ORIGINAL, "3x + 4 = 2x + 19", "distribution_partial"),
            # Jumping ahead must still expose the bracket error hidden in the arithmetic.
            (ORIGINAL, "x = 15", "distribution_partial"),
            (ORIGINAL, "3x - 2x = 19 - 4", "distribution_partial"),
            ("5(x - 2) = 3x + 4", "5x - 2 = 3x + 4", "distribution_partial"),
            ("-2(x - 3) = 4", "-2x - 6 = 4", "distribution_sign"),
            ("-(x + 3) = 4", "-x + 3 = 4", "distribution_sign"),
            ("10 - 3(x + 1) = 1", "10 - 3x + 3 = 1", "distribution_sign"),
            ("3x + 12 = 2x + 19", "3x + 2x = 19 - 12", "transposition_sign"),
            ("3x + 12 = 2x + 19", "x = 19 + 12", "transposition_sign"),
            ("3x + 12 = 2x + 19", "x + 12 = 2x + 19", "one_sided_operation"),
            ("2x = 14", "x = 12", "inverse_operation"),
            ("2x = 14", "x = 28", "inverse_operation"),
            ("4x = 2", "x = 6", "inverse_operation"),
            ("7x = 14", "x = 14", "one_sided_operation"),
            ("6x = 3", "x = 2", "inverted_division"),
            ("5x - 2x = 9", "7x = 9", "arithmetic_slip"),
            ("3x + 12 = 2x + 19", "9x = 100", "unknown"),
        ],
    )
    def test_recognises_reasoning_errors(self, previous, student, expected):
        assert me.diagnose(eq(previous), eq(student)).type == expected

    def test_scenario_evidence_names_the_exact_error(self):
        diagnosis = me.diagnose(eq(ORIGINAL), eq("3x + 4 = 2x + 19"))
        assert diagnosis.skill == "distribution"
        assert diagnosis.evidence["factor"] == "3"
        assert diagnosis.evidence["correct_expansion"] == "3x + 12"
        assert diagnosis.evidence["student_expansion"] == "3x + 4"
        assert diagnosis.evidence["terms_mishandled"] == "4"

    def test_every_mistake_type_maps_to_a_skill(self):
        for skill in me.SKILL_FOR_MISTAKE.values():
            assert isinstance(skill, str) and skill


class TestStepChecks:
    def check(self, *written, last=ORIGINAL, wrong=None):
        return me.check_steps(
            eq(ORIGINAL), eq(last), eq(wrong) if wrong else None, [eq(item) for item in written]
        )

    def test_scenario_error(self):
        [step] = self.check("3x + 4 = 2x + 19")
        assert step.status == "invalid"
        assert step.diagnosis.type == "distribution_partial"

    def test_correct_chain(self):
        steps = self.check("3x + 12 = 2x + 19", "x + 12 = 19", "x = 7")
        assert [step.status for step in steps] == ["valid", "valid", "final_correct"]
        assert steps[-1].substitution == {"x": "7", "left": "33", "right": "33"}

    def test_error_then_consistent_continuation_is_one_mistake(self):
        steps = self.check("3x + 4 = 2x + 19", "x + 4 = 19", "x = 15")
        assert [step.status for step in steps] == ["invalid", "continues_error", "continues_error"]
        assert steps[-1].substitution == {"x": "15", "left": "57", "right": "49"}

    def test_recovery_clears_the_wrong_line(self):
        steps = self.check("3x + 12 = 2x + 19", wrong="3x + 4 = 2x + 19")
        assert steps[0].status == "valid"

    def test_restating_the_problem_is_not_progress(self):
        assert self.check("3(x+4)=2x+19")[0].status == "restated"

    def test_numeric_statements_are_skipped(self):
        assert self.check("3(7 + 4) = 33") == []


class TestStepKinds:
    @pytest.mark.parametrize(
        ("previous", "new", "kind"),
        [
            (ORIGINAL, "3x + 12 = 2x + 19", "opened_brackets"),
            (ORIGINAL, "3x - 2x = 19 - 12", "opened_brackets_and_moved_terms"),
            ("3x + 12 = 2x + 19", "3x - 2x = 19 - 12", "moved_terms"),
            ("3x + 12 = 2x + 19", "x + 12 = 19", "moved_terms"),
            ("3x - 2x = 19 - 12", "x = 7", "simplified_sides"),
            ("2x = 14", "x = 7", "divided_both_sides"),
            ("x / 2 = 3", "x = 6", "cleared_fractions"),
            ("2x = 14", "4x = 28", "multiplied_both_sides"),
        ],
    )
    def test_names_the_students_method(self, previous, new, kind):
        assert me.describe_step(eq(previous), eq(new)) == kind

    def test_kind_is_attached_to_correct_steps(self):
        [step] = me.check_steps(eq(ORIGINAL), eq(ORIGINAL), None, [eq("3x + 12 = 2x + 19")])
        assert step.kind == "opened_brackets"


class TestNumericStatements:
    def test_true_and_false_arithmetic(self):
        written = me.extract_equations("3(7 + 4) = 33, 19*3 = 14 + 19, x = 7")
        checks = me.check_numeric_statements(written)
        assert [c.status for c in checks] == ["true_statement", "false_statement"]
        assert checks[1].diagnosis.evidence["left_value"] == "57"
        assert checks[1].diagnosis.skill == "checking"

    def test_merge_keeps_the_students_order(self):
        written = me.extract_equations("3*4 = 12, 3x + 12 = 2x + 19, 19 - 12 = 7")
        equation_checks = me.check_steps(eq(ORIGINAL), eq(ORIGINAL), None, written)
        merged = me.merge_in_order(written, equation_checks, me.check_numeric_statements(written))
        assert [c.equation.text for c in merged] == [
            "3 * 4 = 12",
            "3x + 12 = 2x + 19",
            "19 - 12 = 7",
        ]


class TestRevealDetection:
    def reveal(self, reply, student=()):
        return me.find_reveal(reply, eq(ORIGINAL), eq(ORIGINAL), [eq(item) for item in student])

    @pytest.mark.parametrize(
        "reply",
        [
            "So the answer is x = 7.",
            "Then $x = 7$.",
            "You should get 3x + 12 = 2x + 19.",
            "The left side is 3x + 12, right?",
            r"Left side: $3 \cdot x + 12$.",
            "It becomes x + 12 = 19",
            # Seen from a real free model: the expansion leaked as a bare number.
            "What happens to the 4? It is also multiplied by 3, so it must become 12.",
            "The answer is 7.",
        ],
    )
    def test_catches_giveaways(self, reply):
        assert self.reveal(reply, student=["3x + 4 = 2x + 19"]) is not None

    @pytest.mark.parametrize(
        "reply",
        [
            "What does the 3 in 3(x + 4) multiply?",
            "Look again at 3(x + 4) = 2x + 19.",
            "You wrote 3x + 4 = 2x + 19. Which terms did the 3 reach?",
            "Compare with a similar case: 2(y + 5). What would 5(x + 1) become?",
            "Try x = 1 in both forms: what do you get?",
            "In the bracket, the factor 3 multiplies both terms. What is $3 \\times 4$?",
            "Step 2 is where it changes. Compare 19 on the right with what you wrote.",
        ],
    )
    def test_allows_guiding_questions(self, reply):
        assert self.reveal(reply, student=["3x + 4 = 2x + 19"]) is None

    def test_secret_number_helper(self):
        assert me.find_secret_number("so it is 20", {sp.Integer(20)}, "") == "20"
        assert me.find_secret_number("so it is 20", {sp.Integer(20)}, "4*5 = 20") is None
        assert me.find_secret_number("1 step", {sp.Integer(1)}, "") is None

    def test_false_statement_values(self):
        checks = me.check_numeric_statements(me.extract_equations("4*5 = 25, 25 = 20"))
        assert me.false_statement_values(checks) == {sp.Integer(20)}

    def test_numbers_the_student_already_wrote_are_not_secrets(self):
        reply = "You said 3 times 4 is 12. So what is the left side now?"
        found = me.find_reveal(reply, eq(ORIGINAL), eq(ORIGINAL), [], ["oh, 3 times 4 is 12"])
        assert found is None

    def test_allows_repeating_what_the_student_already_found(self):
        reply = "Good: 3x + 12 = 2x + 19 is right. What next?"
        assert self.reveal(reply, student=["3x + 12 = 2x + 19"]) is None


class TestProblemAuthoring:
    @pytest.mark.parametrize(
        ("text", "skill", "fragment"),
        [
            ("4(x + 2) = 3x + 11", "distribution", None),
            ("4x + 2 = 3x + 11", "distribution", "bracket"),
            ("2x + 1 = 9", "transposition", "both sides"),
            ("2x = 5", "isolating_variable", "whole number"),
            ("x + 1 = x + 2", "distribution", "linear"),
            ("x = 3", "isolating_variable", "already solved"),
            ("x^2 = 4", "distribution", "linear"),
            ("nonsense", "distribution", "parse"),
        ],
    )
    def test_validation(self, text, skill, fragment):
        reason = me.problem_rejection_reason(text, skill)
        assert (reason is None) if fragment is None else (fragment in reason)

    def test_rejects_repeats(self):
        assert "repeats" in me.problem_rejection_reason(
            "3(x+4) = 2x + 19", "distribution", previous=[ORIGINAL]
        )

    @pytest.mark.parametrize("skill", [*me.SKILLS, "combining_terms", "equation_reasoning"])
    @pytest.mark.parametrize("difficulty", [1, 2, 3])
    def test_generator_always_produces_valid_problems(self, skill, difficulty):
        rng = random.Random(1234)
        seen: list[str] = []
        for _ in range(25):
            problem = me.generate_problem(skill, difficulty, seen[-5:], rng)
            assert me.problem_rejection_reason(problem, skill, seen[-5:]) is None
            seen.append(problem)
