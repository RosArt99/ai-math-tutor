"""The tutoring loop.

Every turn runs in three steps:

1. ``plan_turn``: the engine checks the student's work and decides where the lesson stands.
2. the language model turns those facts into a teaching move, in the student's language.
3. the server verifies the reply (no giveaway answers, sound practice problems) and saves it.

Nothing is saved until the reply exists, so a model failure leaves the lesson untouched and the
student can simply send the same message again.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from . import curriculum, fallbacks
from . import math_engine as me
from .language import gendered_word
from .llm import MISTAKE_TYPES, LLMClient
from .models import Message, Mistake, Problem, Student
from .progress import (
    GENERATED_SKILLS,
    build_progress,
    choose_next_problem,
    mistake_history_for_prompt,
)
from .prompts import CORRECTION_GENDER, CORRECTION_REVEAL, build_messages

logger = logging.getLogger(__name__)

SCENARIO_EQUATION = "3(x + 4) = 2x + 19"
LANGUAGES = {"en": "English", "ru": "Russian"}


def _now() -> datetime:
    return datetime.now(UTC)


def create_problem(
    db: Session,
    student: Student,
    equation: str,
    skill: str,
    origin: str,
    purpose: str = "practice",
    difficulty: int = 1,
    topic: str = "linear",
) -> Problem:
    canonical = curriculum.canonical(topic, equation)
    problem = Problem(
        student=student,
        sequence=len(student.problems),
        equation=canonical,
        last_valid=canonical,
        skill=skill,
        topic=topic,
        origin=origin,
        purpose=purpose,
        difficulty=difficulty,
    )
    db.add(problem)
    return problem


def create_student(db: Session, locale: str = "en") -> Student:
    student = Student(locale=locale)
    db.add(student)
    db.flush()  # Assigns the id that the first problem and message refer to.
    problem = create_problem(db, student, SCENARIO_EQUATION, "distribution", "seed")
    _add_message(db, student, problem, "assistant", fallbacks.topic_opening("linear"))
    db.commit()
    return student


def current_problem(student: Student) -> Problem:
    active = [p for p in student.problems if p.status == "active"]
    return active[-1]


def infer_skill(topic_id: str, text: str) -> str:
    exercised = curriculum.skills_exercised(topic_id, text)
    for skill in curriculum.topic(topic_id).skills:
        if skill in exercised:
            return skill
    return curriculum.topic(topic_id).skills[0]


def custom_problem_error(text: str) -> str | None:
    """Students may bring their own homework, as long as the engine can verify it."""
    if "=" not in text:
        try:
            expression = me.parse_problem_expression(text)
        except me.ParseError:
            return "unparsable"
        if not expression.has_x:
            return "unparsable"
        return "already_solved" if me.is_simplified(expression) else None
    try:
        equation = me.parse_equation(text)
    except me.ParseError:
        return "unparsable"
    solutions = equation.solutions()
    if not equation.has_x or solutions.kind != "finite" or len(solutions.values) != 1:
        return "no_unique_solution"
    return "already_solved" if equation.solved_value() is not None else None


def _abandon_active(student: Student) -> None:
    for problem in student.problems:
        if problem.status == "active":
            problem.status = "abandoned"


def start_custom_problem(db: Session, student: Student, text: str) -> Problem:
    topic_id = curriculum.infer_topic(text)
    canonical = curriculum.canonical(topic_id, text)
    _abandon_active(student)
    problem = create_problem(
        db, student, canonical, infer_skill(topic_id, canonical), "custom", topic=topic_id
    )
    _add_message(db, student, problem, "assistant", fallbacks.custom_opening(student.locale))
    db.commit()
    return problem


def start_topic(db: Session, student: Student, topic_id: str) -> Problem:
    """Open a chapter: its signature example first, then freshly authored problems."""
    chapter = curriculum.topic(topic_id)
    used = [p.equation for p in student.problems]
    first_visit = not any(p.topic == chapter.id for p in student.problems)
    example = curriculum.canonical(chapter.id, chapter.example)
    if first_visit or example not in used:
        text, origin = example, "seed"
    else:
        text = curriculum.generate(chapter.id, chapter.skills[0], 1, used)
        origin = "generator"
    _abandon_active(student)
    problem = create_problem(
        db, student, text, infer_skill(chapter.id, text), origin, topic=chapter.id
    )
    _add_message(db, student, problem, "assistant", fallbacks.topic_opening(chapter.id))
    db.commit()
    return problem


# ---------------------------------------------------------------------------- turn planning


@dataclass
class TurnPlan:
    directive: str
    phase: str
    last_valid: str
    wrong_equation: str | None
    hint_level: int
    check_attempts: int
    steps: list[me.StepCheck] = field(default_factory=list)
    new_diagnosis: me.Diagnosis | None = None
    new_mistake_equation: str | None = None
    resolves_mistakes: bool = False
    completes_problem: bool = False
    check_verified: bool = False


_BOTH_SIDES = re.compile(r"(?i)\b(?:both|each|обе|оба|обеих|каждая|каждой)\b")


def _both_sides_evaluated(text: str, value, numeric: list[me.StepCheck]) -> bool:
    """Whether the student evaluated BOTH sides of the original equation to the same value.

    Accepted: "3(7 + 4) = 2*7 + 19", "33 = 33", two lines that each give 33, "both sides are
    33", or simply "33" in reply to the question. Evaluating one side alone is not a check.
    """
    for step in numeric:
        left, right = step.equation.left, step.equation.right
        computed_both = not isinstance(left, me.Num) and not isinstance(right, me.Num)
        if computed_both and me.substitute(step.equation, 0)[0] == value:
            return True
    occurrences = me.count_number(text, value)
    if occurrences >= 2:
        return True
    if occurrences == 1 and (_BOTH_SIDES.search(text) or me.parse_bare_number(text) == value):
        return True
    return False


def _plan_check_phase(problem: Problem, text: str, original: me.Equation) -> TurnPlan:
    root = me.parse_equation(problem.last_valid).solved_value()
    left, _ = me.substitute(original, root)
    attempts = problem.check_attempts + 1
    written = me.extract_equations(text)
    numeric = me.check_numeric_statements(written)
    false = [step for step in numeric if step.status == "false_statement"]
    # There is no attempt limit: finishing the check for the student would be giving the answer.
    # Help grows with each attempt instead, as it does while correcting an error.
    verified = not false and _both_sides_evaluated(text, left, numeric)
    return TurnPlan(
        directive="complete_problem" if verified else "guide_substitution_check",
        phase="checking",
        last_valid=problem.last_valid,
        wrong_equation=None,
        hint_level=0 if verified else attempts,
        check_attempts=attempts,
        steps=numeric,
        new_diagnosis=false[0].diagnosis if false else None,
        new_mistake_equation=false[0].equation.text if false else None,
        completes_problem=verified,
        check_verified=verified,
    )


def _plan_solving_phase(
    problem: Problem,
    text: str,
    original: me.Equation,
    last_tutor_asked: bool,
) -> TurnPlan:
    written = me.extract_equations(text)
    if not any(equation.has_x for equation in written):
        bare = me.parse_bare_number(text)
        if bare is not None and problem.phase == "solving":
            # A lone number answers the tutor's sub-question unless it could only be the answer.
            is_answer = (
                original.solutions().kind == "finite" and bare in original.solutions().values
            )
            if is_answer or not last_tutor_asked:
                written = [me.Equation(me.Var(), me.Num(_as_fraction(bare)))]

    last_valid = me.parse_equation(problem.last_valid)
    wrong = me.parse_equation(problem.wrong_equation) if problem.wrong_equation else None
    equation_steps = me.check_steps(original, last_valid, wrong, written)
    numeric = me.check_numeric_statements(written)
    steps = me.merge_in_order(written, equation_steps, numeric)
    plan = TurnPlan(
        directive="respond_to_prose",
        phase=problem.phase,
        last_valid=problem.last_valid,
        wrong_equation=problem.wrong_equation,
        hint_level=problem.hint_level,
        check_attempts=0,
        steps=steps,
    )
    was_correcting = problem.phase == "correcting"
    false = [step for step in numeric if step.status == "false_statement"]
    if not equation_steps and false:
        # Only arithmetic was written, and some of it is wrong: that is the error to work on.
        plan.new_diagnosis = false[0].diagnosis
        plan.new_mistake_equation = false[0].equation.text
        plan.phase = "correcting"
        plan.directive = "continue_guiding" if was_correcting else "diagnose_mistake"
        plan.hint_level = problem.hint_level + 1 if was_correcting else 1
        return plan
    steps = equation_steps
    if not steps:
        if was_correcting:
            plan.directive = "continue_guiding"
            plan.hint_level = problem.hint_level + 1
        return plan

    for step in steps:
        if step.status in ("valid", "final_correct", "restated"):
            plan.last_valid = step.equation.text
            plan.wrong_equation = None
        else:
            plan.wrong_equation = step.equation.text
        if step.status == "invalid" and plan.new_diagnosis is None:
            plan.new_diagnosis = step.diagnosis
            plan.new_mistake_equation = step.equation.text

    plan.steps = me.merge_in_order(written, equation_steps, numeric)
    last = steps[-1]
    if last.status in ("invalid", "continues_error"):
        plan.phase = "correcting"
        if plan.new_diagnosis is not None:
            plan.directive = "continue_guiding" if was_correcting else "diagnose_mistake"
            plan.hint_level = problem.hint_level + 1 if was_correcting else 1
        else:
            plan.directive = "continues_error"
            plan.hint_level = max(problem.hint_level, 1)
        return plan

    if last.status == "final_correct":
        plan.phase = "checking"
        plan.directive = "ask_substitution_check"
        plan.resolves_mistakes = True
        plan.hint_level = 0
    elif last.status == "valid":
        plan.phase = "solving"
        plan.directive = "mistake_corrected" if was_correcting else "acknowledge_progress"
        plan.resolves_mistakes = was_correcting
        plan.hint_level = 0
    else:  # restated
        plan.directive = "continue_guiding" if was_correcting else "restated"
        if was_correcting:
            plan.hint_level = problem.hint_level + 1
    return plan


def _as_fraction(value) -> me.Fraction:
    return me.Fraction(int(value.p), int(value.q))


def _plan_expression_turn(problem: Problem, text: str) -> TurnPlan:
    """Expand-and-simplify problems: the chain of expressions must keep the original's value."""
    original = me.parse_problem_expression(problem.equation)
    last_valid = me.parse_problem_expression(problem.last_valid)
    wrong = me.parse_problem_expression(problem.wrong_equation) if problem.wrong_equation else None
    written = [
        item for item in me.extract_expression_chain(text) if item.text != original.text
    ] or me.extract_expression_chain(text)
    steps = me.check_expression_steps(original, last_valid, wrong, written)
    was_correcting = problem.phase == "correcting"
    plan = TurnPlan(
        directive="respond_to_prose",
        phase=problem.phase,
        last_valid=problem.last_valid,
        wrong_equation=problem.wrong_equation,
        hint_level=problem.hint_level,
        check_attempts=0,
        steps=steps,
    )
    if not steps:
        if was_correcting:
            plan.directive = "continue_guiding"
            plan.hint_level = problem.hint_level + 1
        return plan
    for step in steps:
        if step.status in ("valid", "final_correct", "restated"):
            plan.last_valid = step.equation.text
            plan.wrong_equation = None
        else:
            plan.wrong_equation = step.equation.text
        if step.status == "invalid" and plan.new_diagnosis is None:
            plan.new_diagnosis = step.diagnosis
            plan.new_mistake_equation = step.equation.text
    last = steps[-1]
    if last.status in ("invalid", "continues_error"):
        plan.phase = "correcting"
        if plan.new_diagnosis is not None:
            plan.directive = "continue_guiding" if was_correcting else "diagnose_mistake"
            plan.hint_level = problem.hint_level + 1 if was_correcting else 1
        else:
            plan.directive = "continues_error"
            plan.hint_level = max(problem.hint_level, 1)
    elif last.status == "final_correct":
        # A fully simplified, equivalent expression finishes the problem.
        plan.phase = "solving"
        plan.directive = "complete_problem"
        plan.resolves_mistakes = True
        plan.completes_problem = True
        plan.check_verified = True
        plan.hint_level = 0
    elif last.status == "valid":
        plan.phase = "solving"
        plan.directive = "mistake_corrected" if was_correcting else "acknowledge_progress"
        plan.resolves_mistakes = was_correcting
        plan.hint_level = 0
    else:
        plan.directive = "continue_guiding" if was_correcting else "restated"
        if was_correcting:
            plan.hint_level = problem.hint_level + 1
    return plan


def plan_turn(problem: Problem, text: str, last_tutor_asked: bool) -> TurnPlan:
    if curriculum.topic(problem.topic).mode == "expression":
        return _plan_expression_turn(problem, text)
    original = me.parse_equation(problem.equation)
    if problem.phase == "checking":
        return _plan_check_phase(problem, text, original)
    return _plan_solving_phase(problem, text, original, last_tutor_asked)


# ---------------------------------------------------------------------------- facts for the model


def _build_facts(
    student: Student,
    problem: Problem,
    plan: TurnPlan,
    active_mistake: Mistake | None,
    next_request: dict | None,
) -> dict:
    chapter = curriculum.topic(problem.topic)
    facts: dict = {
        "reply_language": LANGUAGES.get(student.locale, "English"),
        "topic": chapter.title,
        "task": chapter.task,
        "problem": problem.equation,
        "problem_purpose": problem.purpose,
        "directive": plan.directive,
        "hint_level": plan.hint_level,
        "last_correct_equation": plan.last_valid,
        "student_steps": [
            {
                "equation": step.equation.text,
                "status": step.status,
                **({"kind": step.kind} if step.kind else {}),
                **({"substituting_into_original": step.substitution} if step.substitution else {}),
            }
            for step in plan.steps
        ],
        "mistake_history": mistake_history_for_prompt(student),
    }
    if chapter.mode == "expression":
        simplified = me.parse_problem_expression(problem.equation).value
        facts["private"] = {"simplified_result": me.format_expr(simplified)}
        facts["last_correct_expression"] = facts.pop("last_correct_equation")
    else:
        original = me.parse_equation(problem.equation)
        facts["private"] = {
            "solutions": [me.format_expr(value) for value in original.solutions().values]
        }
    diagnosis = plan.new_diagnosis
    if diagnosis is not None:
        facts["diagnosis"] = {"type": diagnosis.type, "evidence": diagnosis.evidence}
    elif active_mistake is not None and plan.phase == "correcting":
        facts["diagnosis"] = {"type": active_mistake.type, "evidence": active_mistake.evidence}
    if (
        chapter.mode == "equation"
        and plan.phase == "checking"
        and (plan.directive != "ask_substitution_check")
    ):
        root = me.parse_equation(plan.last_valid).solved_value()
        left, right = me.substitute(original, root)
        facts["check"] = {
            "solution": me.format_expr(root),
            "left_side_value": me.format_expr(left),
            "right_side_value": me.format_expr(right),
            "verified_by_student": plan.check_verified,
        }
    if next_request is not None:
        next_chapter = curriculum.topic(next_request["topic"])
        facts["next_problem_request"] = {
            **next_request,
            "topic": next_chapter.title,
            "format": (
                "an equation in x, such as 4(x + 2) = 3x + 11"
                if next_chapter.mode == "equation"
                else "an expression in x with no equals sign, such as (x + 2)^2 - x(x + 1)"
            ),
            "similar_to": problem.equation,
            "instruction": (
                "Same structure and skill, different numbers: it verifies this student's "
                "understanding of the error just corrected."
                if next_request["purpose"] == "follow_up"
                else "Slightly harder than the last problem, or practise the named skill."
            ),
        }
    return facts


# ---------------------------------------------------------------------------- reply verification


def _reveal_in_reply(
    reply: str,
    problem: Problem,
    plan: TurnPlan,
    next_problem: Problem | None,
    student_text: str,
) -> str | None:
    if next_problem is not None:
        if curriculum.topic(next_problem.topic).mode == "expression":
            item = me.parse_problem_expression(next_problem.equation)
            return me.find_expression_reveal(reply, item, item, [])
        equation = me.parse_equation(next_problem.equation)
        return me.find_reveal(reply, equation, equation, [])
    if curriculum.topic(problem.topic).mode == "expression":
        student_texts = [m.content for m in problem.messages if m.role == "user"] + [student_text]
        known = [step.equation for step in plan.steps]
        for content in student_texts:
            known += me.extract_expression_chain(content)
        return me.find_expression_reveal(
            reply,
            me.parse_problem_expression(problem.equation),
            me.parse_problem_expression(plan.last_valid),
            known,
        )
    if plan.phase == "checking":
        return _reveal_in_check(reply, problem, plan)
    student_texts = [m.content for m in problem.messages if m.role == "user"] + [student_text]
    # Numbers the engine itself offers as a hint, such as a common denominator, may be said.
    if plan.new_diagnosis is not None:
        student_texts.append(" ".join(plan.new_diagnosis.evidence.values()))
    student_equations = [
        step.equation
        for step in plan.steps
        if step.status in ("valid", "final_correct", "restated")
    ]
    for content in student_texts:
        student_equations += me.extract_equations(content)
    # A wrong equation the student wrote can be quoted freely when pointing out the error.
    student_equations += [step.equation for step in plan.steps]
    return me.find_reveal(
        reply,
        me.parse_equation(problem.equation),
        me.parse_equation(plan.last_valid),
        student_equations,
        student_texts,
    )


def _reveal_in_check(reply: str, problem: Problem, plan: TurnPlan) -> str | None:
    """During the check, the values of the sides are what the student must compute."""
    if plan.directive != "guide_substitution_check":
        return None
    original = me.parse_equation(problem.equation)
    root = me.parse_equation(plan.last_valid).solved_value()
    secrets = set(me.substitute(original, root)) | me.false_statement_values(plan.steps)
    # Only arithmetic the student got right counts as already known; a value that appears
    # in a false line ("25 = 20") has not been established by them.
    true_lines = [step.equation.text for step in plan.steps if step.status == "true_statement"]
    for message in problem.messages:
        for step in message.meta.get("steps", []):
            if step.get("status") == "true_statement":
                true_lines.append(step["equation"])
    return me.find_secret_number(reply, secrets, " ".join(true_lines))


def _accepted_model_problem(data: dict, request: dict, used: list[str]) -> str | None:
    proposal = data.get("new_problem")
    text = proposal.get("equation") if isinstance(proposal, dict) else None
    if not isinstance(text, str):
        return None
    topic_id, skill = request["topic"], request["skill"]
    structural = (
        skill if topic_id == "linear" or skill in ("special_products", "distribution") else ""
    )
    reason = curriculum.rejection_reason(topic_id, text, structural, used)
    if reason is not None:
        logger.info("Rejected model problem %r: %s", text, reason)
        return None
    return curriculum.canonical(topic_id, text)


# ---------------------------------------------------------------------------- the turn


def _add_message(
    db: Session,
    student: Student,
    problem: Problem,
    role: str,
    content: str,
    meta: dict | None = None,
) -> Message:
    message = Message(
        student_id=student.id,
        problem=problem,
        sequence=len(problem.messages),
        role=role,
        content=content,
        meta=meta or {},
    )
    db.add(message)
    return message


def _record_mistakes(
    db: Session,
    student: Student,
    problem: Problem,
    plan: TurnPlan,
    data: dict,
    active: Mistake | None,
) -> None:
    reported = data.get("reasoning_mistake")
    reported = reported if isinstance(reported, dict) else None
    explanation = reported.get("explanation") if reported else None
    explanation = explanation if isinstance(explanation, str) else None

    diagnosis = plan.new_diagnosis
    source = "engine"
    # With nothing for the engine to check, the model's reading of the student's words counts.
    if (
        diagnosis is None
        and reported
        and plan.directive == "respond_to_prose"
        and active is None
        and reported.get("type") in MISTAKE_TYPES
    ):
        kind = reported["type"]
        diagnosis = me.Diagnosis(kind, me.SKILL_FOR_MISTAKE.get(kind, problem.skill))
        source = "model"
        plan.phase = "correcting"
        plan.hint_level = 1

    if diagnosis is None:
        if active is not None and explanation and not active.explanation:
            active.explanation = explanation
        if active is not None and plan.phase == "correcting":
            active.attempts += 1
        return

    if active is not None and active.type == diagnosis.type:
        active.attempts += 1
        active.equation = plan.new_mistake_equation or active.equation
        if explanation and not active.explanation:
            active.explanation = explanation
        return

    db.add(
        Mistake(
            student=student,
            sequence=len(student.mistakes),
            problem_id=problem.id,
            type=diagnosis.type,
            skill=curriculum.skill_for_topic(problem.topic, diagnosis.skill, problem.skill),
            source=source,
            equation=plan.new_mistake_equation,
            evidence=diagnosis.evidence,
            explanation=explanation,
        )
    )
    problem.mistake_count += 1


def _resolve_open_mistakes(student: Student, problem: Problem) -> None:
    for mistake in student.mistakes:
        if mistake.problem_id == problem.id and mistake.resolved_at is None:
            mistake.resolved_at = _now()


def handle_turn(
    db: Session, llm: LLMClient, student: Student, text: str, history_limit: int
) -> dict:
    """Process one student message. Raises LLMError with nothing saved if the model fails."""
    problem = current_problem(student)
    prior = [m for p in student.problems for m in p.messages][-history_limit:]
    last_tutor_asked = bool(prior and prior[-1].role == "assistant" and "?" in prior[-1].content)
    active = next(
        (
            m
            for m in reversed(student.mistakes)
            if m.problem_id == problem.id and m.resolved_at is None
        ),
        None,
    )
    plan = plan_turn(problem, text, last_tutor_asked)

    # When the problem is finished, decide what follows; it does not depend on the model.
    used = [p.equation for p in student.problems]
    next_request: dict | None = None
    follow_up_for: str | None = None
    if plan.completes_problem:
        if problem.mistake_count > 0 or plan.new_diagnosis is not None:
            if plan.new_diagnosis is not None:
                skill = plan.new_diagnosis.skill
            else:
                skill = next(
                    m for m in reversed(student.mistakes) if m.problem_id == problem.id
                ).skill
            mapped = curriculum.skill_for_topic(problem.topic, skill, problem.skill)
            # Practise what went wrong if problems can target it, otherwise the same kind again.
            generated = GENERATED_SKILLS.get(problem.topic, ())
            follow_up_for = mapped if mapped in generated else problem.skill
            next_request = {
                "topic": problem.topic,
                "skill": follow_up_for,
                "difficulty": problem.difficulty,
                "purpose": "follow_up",
            }
        else:
            next_request = choose_next_problem(student, problem)

    facts = _build_facts(student, problem, plan, active, next_request)
    history = [(m.role, m.content) for m in prior]
    result = llm.complete(build_messages(history, text, facts))
    data = result.data

    next_equation_text: str | None = None
    origin = "generator"
    if next_request is not None:
        next_equation_text = _accepted_model_problem(data, next_request, used)
        if next_equation_text is not None:
            origin = "model"
        else:
            next_equation_text = curriculum.generate(
                next_request["topic"], next_request["skill"], next_request["difficulty"], used
            )
    next_preview = (
        Problem(equation=next_equation_text, topic=next_request["topic"])
        if next_equation_text and next_request
        else None
    )

    # Reply checks: one chance to rewrite. A giveaway that survives the rewrite is replaced by
    # a prepared hint; a wording slip that survives is accepted, as it costs no learning.
    reply = data["message"].strip()
    reply_source = "model"
    fragment = _reveal_in_reply(reply, problem, plan, next_preview, text)
    gendered = gendered_word(reply, student.locale)
    if fragment is not None or gendered is not None:
        corrections = []
        if fragment is not None:
            logger.info("Reply revealed %r; asking the model to rewrite", fragment)
            corrections.append(CORRECTION_REVEAL.format(fragment=fragment))
        if gendered is not None:
            logger.info("Reply assumed the student's gender (%r); asking for a rewrite", gendered)
            corrections.append(CORRECTION_GENDER.format(word=gendered))
        retried = llm.complete(
            build_messages(history, text, facts, correction=" ".join(corrections))
        )
        rewritten = retried.data["message"].strip()
        if _reveal_in_reply(rewritten, problem, plan, next_preview, text) is None:
            reply = rewritten
            reply_source = "model_rewrite"
            data = {**retried.data, "new_problem": data.get("new_problem")}
        else:
            reply = fallbacks.safe_hint(
                student.locale, plan.directive, plan.new_diagnosis or active
            )
            reply_source = "guard_fallback"

    # ---- Everything below only runs once a reply exists.
    _add_message(
        db,
        student,
        problem,
        "user",
        text,
        {
            "steps": [
                {
                    "equation": step.equation.text,
                    "latex": step.equation.latex,
                    "status": step.status,
                    "kind": step.kind,
                    "mistake": step.diagnosis.type if step.diagnosis else None,
                }
                for step in plan.steps
            ]
        },
    )
    _record_mistakes(db, student, problem, plan, data, active)
    db.flush()

    problem.phase = plan.phase
    problem.last_valid = plan.last_valid
    problem.wrong_equation = plan.wrong_equation
    problem.hint_level = plan.hint_level
    problem.check_attempts = plan.check_attempts
    if plan.resolves_mistakes:
        _resolve_open_mistakes(student, problem)
    _add_message(
        db,
        student,
        problem,
        "assistant",
        reply,
        {"directive": plan.directive, "model": result.model, "source": reply_source},
    )

    completed_problem: Problem | None = None
    if plan.completes_problem and next_equation_text is not None and next_request is not None:
        problem.status = "completed"
        problem.completed_at = _now()
        problem.check_verified = plan.check_verified
        _resolve_open_mistakes(student, problem)
        completed_problem = problem
        create_problem(
            db,
            student,
            next_equation_text,
            next_request["skill"],
            origin,
            next_request["purpose"],
            next_request["difficulty"],
            next_request["topic"],
        )
    db.commit()
    db.refresh(student)
    logger.info(
        "turn student=%s directive=%s model=%s source=%s follow_up_for=%s",
        student.id[:8],
        plan.directive,
        result.model,
        reply_source,
        follow_up_for,
    )
    return {
        "reply": reply,
        "directive": plan.directive,
        "problem_completed": completed_problem is not None,
    }


# ---------------------------------------------------------------------------- views


def problem_view(problem: Problem) -> dict:
    chapter = curriculum.topic(problem.topic)
    return {
        "id": problem.id,
        "equation": problem.equation,
        "latex": curriculum.latex(problem.topic, problem.equation),
        "topic": chapter.id,
        "topic_number": chapter.number,
        "topic_title": chapter.title,
        "task": chapter.task,
        "skill": problem.skill,
        "difficulty": problem.difficulty,
        "purpose": problem.purpose,
        "origin": problem.origin,
        "phase": problem.phase,
        "mistake_count": problem.mistake_count,
    }


def state_view(student: Student) -> dict:
    problems = student.problems
    messages = [
        {
            "id": message.id,
            "role": message.role,
            "content": message.content,
            "problem_id": problem.id,
            "steps": message.meta.get("steps", []),
            "source": message.meta.get("source"),
            "model": message.meta.get("model"),
        }
        for problem in problems
        for message in problem.messages
    ]
    return {
        "locale": student.locale,
        "problem": problem_view(current_problem(student)),
        "problems": [problem_view(problem) for problem in problems],
        "messages": messages,
        "progress": build_progress(student),
    }
