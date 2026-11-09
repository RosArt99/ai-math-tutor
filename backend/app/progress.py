"""Learning analytics: a record book organised like the chapters of an algebra textbook."""

from __future__ import annotations

from collections import Counter

from . import curriculum
from .models import Problem, Student

RECENT_WINDOW = 5
GENERATED_SKILLS = {
    "linear": ("distribution", "transposition", "isolating_variable", "combining_terms"),
    "fractions": ("clearing_fractions",),
    "expressions": ("special_products", "distribution", "combining_terms"),
}


def _grade(clean_ratio: float) -> int:
    """Five-point scale from the share of recent problems done without this kind of error."""
    if clean_ratio >= 0.8:
        return 5
    if clean_ratio >= 0.6:
        return 4
    if clean_ratio >= 0.4:
        return 3
    return 2


def _topic_report(student: Student, topic_id: str, finishing: Problem | None = None) -> dict:
    chapter = curriculum.topic(topic_id)
    problems = [p for p in student.problems if p.topic == topic_id]
    completed = [p for p in problems if p.status == "completed" or p is finishing]
    mistakes_by_problem: dict[str, set[str]] = {}
    for mistake in student.mistakes:
        mistakes_by_problem.setdefault(mistake.problem_id, set()).add(mistake.skill)

    skills = []
    for skill in chapter.skills:
        attempts = [
            p for p in completed if skill in curriculum.skills_exercised(topic_id, p.equation)
        ]
        recent = attempts[-RECENT_WINDOW:]
        clean = [p for p in recent if skill not in mistakes_by_problem.get(p.id, set())]
        errors = sum(
            1
            for m in student.mistakes
            if m.skill == skill and any(m.problem_id == p.id for p in problems)
        )
        if not attempts:
            status = "needs_practice" if errors else "new"
        elif skill in mistakes_by_problem.get(attempts[-1].id, set()):
            status = "needs_practice"
        else:
            status = "confirmed" if errors else "strong"
        skills.append(
            {
                "skill": skill,
                "title": curriculum.SKILL_TITLES.get(skill, skill),
                "grade": _grade(len(clean) / len(recent)) if recent else None,
                "attempts": len(attempts),
                "mistakes": errors,
                "status": status,
            }
        )
    return {
        "id": chapter.id,
        "number": chapter.number,
        "title": chapter.title,
        "summary": chapter.summary,
        "task": chapter.task,
        "example": chapter.example,
        "example_latex": curriculum.latex(chapter.id, chapter.example),
        "completed": len([p for p in problems if p.status == "completed"]),
        "skills": skills,
    }


def build_progress(student: Student) -> dict:
    completed = [p for p in student.problems if p.status == "completed"]
    clean = [p for p in completed if p.mistake_count == 0]
    types = Counter(m.type for m in student.mistakes)
    follow_ups = [p for p in completed if p.purpose == "follow_up"]
    return {
        "completed": len(completed),
        "clean": len(clean),
        "mistakes_total": len(student.mistakes),
        "mistakes_open": sum(m.resolved_at is None for m in student.mistakes),
        "follow_ups_passed": sum(p.mistake_count == 0 for p in follow_ups),
        "follow_ups_total": len(follow_ups),
        "by_type": [{"type": name, "count": count} for name, count in types.most_common()],
        "topics": [_topic_report(student, topic_id) for topic_id in curriculum.TOPICS],
        "recent_mistakes": [
            {
                "type": m.type,
                "skill": m.skill,
                "equation": m.equation,
                "explanation": m.explanation,
                "resolved": m.resolved_at is not None,
            }
            for m in student.mistakes[-5:][::-1]
        ],
    }


def mistake_history_for_prompt(student: Student, limit: int = 6) -> list[dict]:
    """Earlier errors, so explanations can connect to what this student tends to do."""
    return [
        {"type": m.type, "equation": m.equation, "resolved": m.resolved_at is not None}
        for m in student.mistakes[-limit:]
    ]


def choose_next_problem(student: Student, finished: Problem) -> dict:
    """Stay in the chapter: weakest skill first, otherwise climb in difficulty."""
    topic_id = finished.topic
    report = _topic_report(student, topic_id, finished)
    generated = GENERATED_SKILLS.get(topic_id, ())
    for item in report["skills"]:
        if item["status"] == "needs_practice" and item["skill"] in generated:
            return {
                "topic": topic_id,
                "skill": item["skill"],
                "difficulty": finished.difficulty,
                "purpose": "practice",
            }
    if finished.difficulty < 3:
        return {
            "topic": topic_id,
            "skill": finished.skill if finished.skill in generated else generated[0],
            "difficulty": finished.difficulty + 1,
            "purpose": "practice",
        }
    position = generated.index(finished.skill) if finished.skill in generated else -1
    return {
        "topic": topic_id,
        "skill": generated[(position + 1) % len(generated)],
        "difficulty": 3,
        "purpose": "practice",
    }
