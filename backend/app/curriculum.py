"""The course: topics as chapters of an algebra book, each with the skills it trains.

Every topic says how its problems are checked ("equation" or "expression" mode), which skills it
grades, how to recognise that a problem exercises a skill, and how to author verified problems.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import sympy as sp

from . import math_engine as me

SKILL_TITLES = {
    "distribution": "Opening brackets",
    "transposition": "Moving terms across =",
    "combining_terms": "Combining like terms",
    "isolating_variable": "Finding x",
    "checking": "Checking the answer",
    "clearing_fractions": "Clearing denominators",
    "special_products": "Square of a sum or difference",
}


@dataclass(frozen=True)
class Topic:
    id: str
    number: int
    title: str
    summary: str
    mode: str  # "equation" | "expression"
    example: str
    skills: tuple[str, ...]

    @property
    def task(self) -> str:
        return "Solve the equation" if self.mode == "equation" else "Expand and simplify"


TOPICS: dict[str, Topic] = {
    topic.id: topic
    for topic in (
        Topic(
            id="linear",
            number=1,
            title="Linear equations",
            summary="x on both sides, brackets to open",
            mode="equation",
            example="3(x + 4) = 2x + 19",
            skills=(
                "distribution",
                "transposition",
                "combining_terms",
                "isolating_variable",
                "checking",
            ),
        ),
        Topic(
            id="fractions",
            number=2,
            title="Equations with fractions",
            summary="clear the denominators first",
            mode="equation",
            example="x/2 + x/3 = 10",
            skills=("clearing_fractions", "combining_terms", "isolating_variable", "checking"),
        ),
        Topic(
            id="expressions",
            number=3,
            title="Expanding and simplifying",
            summary="square of a sum, then like terms",
            mode="expression",
            example="(x + 3)^2 - x(x - 2)",
            skills=("special_products", "distribution", "combining_terms"),
        ),
    )
}


def topic(topic_id: str) -> Topic:
    return TOPICS.get(topic_id, TOPICS["linear"])


def canonical(topic_id: str, text: str) -> str:
    if topic(topic_id).mode == "expression":
        return me.parse_problem_expression(text).text
    return me.parse_equation(text).text


def latex(topic_id: str, text: str) -> str:
    if topic(topic_id).mode == "expression":
        return me.parse_problem_expression(text).latex
    return me.parse_equation(text).latex


def skill_for_topic(topic_id: str, skill: str, fallback: str) -> str:
    """Map an engine skill onto one this topic grades, so every error lands in the record."""
    skills = topic(topic_id).skills
    if skill in skills:
        return skill
    if skill in ("equality_balance",) and "transposition" in skills:
        return "transposition"
    if skill == "checking" and "checking" not in skills:
        return "combining_terms"
    return fallback if fallback in skills else skills[0]


# ------------------------------------------------------------------------ skill recognition


def _has_division(node: me.Node) -> bool:
    return bool(me._denominators(node))


def _has_square(node: me.Node) -> bool:
    match node:
        case me.Pow(base, _):
            return isinstance(base, me.Add | me.Sub)
        case me.Add(left, right) | me.Sub(left, right) | me.Mul(left, right) | me.Div(left, right):
            return _has_square(left) or _has_square(right)
        case me.Neg(operand):
            return _has_square(operand)
    return False


def _has_bracket_product(node: me.Node) -> bool:
    match node:
        case me.Mul(left, right):
            return (
                isinstance(left, me.Add | me.Sub)
                or isinstance(right, me.Add | me.Sub)
                or _has_bracket_product(left)
                or _has_bracket_product(right)
            )
        case me.Add(left, right) | me.Sub(left, right) | me.Div(left, right):
            return _has_bracket_product(left) or _has_bracket_product(right)
        case me.Neg(operand):
            return isinstance(operand, me.Add | me.Sub) or _has_bracket_product(operand)
    return False


def skills_exercised(topic_id: str, text: str) -> set[str]:
    """Which of a topic's skills a given problem actually calls on."""
    current = topic(topic_id)
    exercised = {"combining_terms"}
    if current.mode == "expression":
        node = me.parse_problem_expression(text).node
        if _has_square(node):
            exercised.add("special_products")
        if _has_bracket_product(node):
            exercised.add("distribution")
        return exercised & set(current.skills)
    equation = me.parse_equation(text)
    exercised |= {"isolating_variable", "checking"}
    if me._has_distribution_site(equation.left) or me._has_distribution_site(equation.right):
        exercised.add("distribution")
    if me.contains_x(equation.left) and me.contains_x(equation.right):
        exercised.add("transposition")
    if _has_division(equation.left) or _has_division(equation.right):
        exercised.add("clearing_fractions")
    return exercised & set(current.skills)


# ------------------------------------------------------------------------ problem authoring


def rejection_reason(topic_id: str, text: str, skill: str, previous: list[str]) -> str | None:
    """Why a proposed problem cannot be used for this topic, or None if it is sound."""
    current = topic(topic_id)
    if current.mode == "equation":
        engine_skill = skill if skill in me.GENERATABLE_SKILLS else "equation_reasoning"
        reason = me.problem_rejection_reason(text, engine_skill, previous)
        if reason:
            return reason
        equation = me.parse_equation(text)
        has_fraction = _has_division(equation.left) or _has_division(equation.right)
        if topic_id == "fractions" and not has_fraction:
            return "must contain a fraction with a numeric denominator"
        if topic_id == "linear" and has_fraction:
            return "fractions belong to the next topic"
        return None
    try:
        expression = me.parse_problem_expression(text)
    except me.ParseError as error:
        return f"cannot parse: {error}"
    if not expression.has_x:
        return "does not contain x"
    value = expression.value
    if sp.Poly(value, me.X).degree() > 2:
        return "the simplified result must be at most quadratic"
    if me.is_simplified(expression):
        return "is already simplified"
    if skill == "special_products" and not _has_square(expression.node):
        return "must contain the square of a sum or difference"
    if skill == "distribution" and not me._has_brackets(expression.node):
        return "must contain a bracket to open"
    if len(sp.Add.make_args(value)) > 3:
        return "the result should have at most three terms"
    for known in previous:
        try:
            if me.parse_problem_expression(known).text == expression.text:
                return "repeats an earlier problem"
        except me.ParseError:
            continue
    return None


def _signed(value: int) -> str:
    return f"+ {value}" if value >= 0 else f"- {-value}"


def _fraction_problem(rng: random.Random, difficulty: int) -> str:
    a, b = rng.sample([2, 3, 4, 5, 6], 2)
    lcm = int(sp.ilcm(a, b))
    if difficulty == 1:
        root = lcm * rng.randint(1, 4)
        return f"x/{a} + x/{b} = {root // a + root // b}"
    if difficulty == 2:
        # (x + p)/a = (x - q)/b with a whole-number solution: b(x + p) = a(x - q).
        while True:
            p, q = rng.randint(1, 9), rng.randint(1, 9)
            if a == b:
                continue
            root = sp.Rational(-(b * p + a * q), b - a)
            if root.is_integer and 0 < abs(root) <= 40:
                return f"(x + {p})/{a} = (x - {q})/{b}"
    root = lcm * rng.randint(1, 3)
    c = rng.randint(1, 9)
    result = sp.Rational(root, a) - sp.Rational(root - c, b)
    if not result.is_integer:
        return _fraction_problem(rng, 1)
    return f"x/{a} - (x - {c})/{b} = {result}"


def _expression_problem(rng: random.Random, skill: str, difficulty: int) -> str:
    a = rng.randint(2, 7)
    b = rng.choice([value for value in range(1, 10) if value != 2 * a])
    if skill == "distribution":
        c, d = rng.randint(2, 6), rng.randint(1, 9)
        return f"{a}(x + {b}) - {c}(x - {d})"
    if skill == "combining_terms":
        return f"{a}x {_signed(b)} - x + {rng.randint(2, 5)}x - {rng.randint(1, 9)}"
    if difficulty == 1:
        return f"(x + {a})^2 - x(x + {b})"
    if difficulty == 2:
        return f"(x - {a})^2 - x(x - {b})"
    return f"(2x + {a})^2 - 4x(x + {b})"


def generate(
    topic_id: str,
    skill: str,
    difficulty: int,
    previous: list[str],
    rng: random.Random | None = None,
) -> str:
    """Author a verified problem for a topic without a language model."""
    source = rng or random.Random()
    level = max(1, min(3, difficulty))
    for _ in range(80):
        if topic_id == "fractions":
            candidate = _fraction_problem(source, level)
        elif topic_id == "expressions":
            candidate = _expression_problem(source, skill, level)
        else:
            engine_skill = skill if skill in me.GENERATABLE_SKILLS else "distribution"
            candidate = me.generate_problem(engine_skill, level, previous, source)
        check_skill = skill if skill in topic(topic_id).skills else topic(topic_id).skills[0]
        if topic_id != "linear":
            check_skill = check_skill if check_skill in ("special_products", "distribution") else ""
        if rejection_reason(topic_id, candidate, check_skill, previous) is None:
            return canonical(topic_id, candidate)
    raise RuntimeError(f"Could not generate a problem for {topic_id}/{skill}")


def infer_topic(text: str) -> str:
    """Place a student's own problem in the right chapter."""
    if "=" not in text:
        return "expressions"
    equation = me.parse_equation(text)
    if _has_division(equation.left) or _has_division(equation.right):
        return "fractions"
    return "linear"
