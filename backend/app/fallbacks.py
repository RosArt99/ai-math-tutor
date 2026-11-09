"""Prepared text for moments that must not depend on a model reply.

These cover the fixed lesson openings, and the safety net used when a model twice tries to reveal
the answer. Ordinary tutoring replies always come from the model.
"""

from __future__ import annotations

_OPENING = {
    "en": (
        "Let's solve this equation together. Write your working step by step, one equation at a "
        "time, and I'll follow your reasoning. What is your first step?"
    ),
    "ru": (
        "Решим это уравнение вместе. Записывай решение по шагам, по одному уравнению за раз, а я "
        "буду следить за ходом твоих рассуждений. С чего начнёшь?"
    ),
}

_CUSTOM_OPENING = {
    "en": "A good equation to work on. Write your first step, and tell me why you chose it.",
    "ru": "Хорошее уравнение. Запиши первый шаг и объясни, почему начинаешь именно с него.",
}

_HINTS_BY_MISTAKE = {
    "en": {
        "distribution_partial": (
            "Look at the number outside the bracket. Which terms inside the bracket does it "
            "multiply: one of them or all of them?"
        ),
        "distribution_sign": (
            "Look at the sign in front of the bracket. What happens to the sign of each term "
            "inside when you open it?"
        ),
        "transposition_sign": (
            "When a term crosses the equals sign, what must happen to its sign, and why does "
            "that keep both sides balanced?"
        ),
        "one_sided_operation": (
            "An equation is like a balance. You changed one side, so what must you do to the "
            "other to keep it level?"
        ),
        "inverse_operation": (
            "What operation connects the coefficient and x here? Which operation undoes it?"
        ),
        "false_equality": (
            "Look at each line of your calculation. Is the left side really equal to the "
            "right side in every one?"
        ),
        "inverted_division": (
            "Which number should be divided by which? Try your result in the previous equation "
            "and see what happens."
        ),
    },
    "ru": {
        "distribution_partial": (
            "Посмотри на число перед скобкой. Какие слагаемые в скобке оно умножает: одно или все?"
        ),
        "distribution_sign": (
            "Посмотри на знак перед скобкой. Что происходит со знаком каждого слагаемого, когда "
            "скобка раскрывается?"
        ),
        "transposition_sign": (
            "Что должно случиться со знаком слагаемого при переносе через знак равенства и "
            "почему равенство при этом сохраняется?"
        ),
        "one_sided_operation": (
            "Уравнение похоже на весы. Одна часть изменилась. Что нужно сделать с другой, чтобы "
            "равновесие сохранилось?"
        ),
        "inverse_operation": (
            "Какое действие связывает коэффициент и x? Какое действие ему обратно?"
        ),
        "false_equality": (
            "Посмотри на каждую строку вычислений. В каждой ли левая часть действительно "
            "равна правой?"
        ),
        "inverted_division": (
            "Какое число на какое нужно делить? Подставь свой результат в предыдущее уравнение и "
            "посмотри, что получится."
        ),
    },
}

_GENERAL_HINT = {
    "en": "Let's go back to your last step. Which rule allowed it? Does it apply to every term?",
    "ru": "Вернёмся к последнему шагу. Какое правило его разрешает? Оно верно для всех слагаемых?",
}

_DIRECTIVE_HINTS = {
    "complete_problem": {
        "en": "Well done: the check works out. Here is your next problem.",
        "ru": "Молодец, проверка сошлась. Вот следующая задача.",
    },
    "ask_substitution_check": {
        "en": "Now check it: substitute your answer into the original equation. What is each side?",
        "ru": "Теперь проверка: подставь ответ в исходное уравнение. Чему равна каждая часть?",
    },
    "acknowledge_progress": {
        "en": "That step is valid. What will you do next, and why?",
        "ru": "Этот шаг верный. Что сделаешь дальше и почему?",
    },
    "mistake_corrected": {
        "en": "Yes, that step is now correct. What comes next?",
        "ru": "Да, теперь этот шаг верный. Что дальше?",
    },
}


def _language(locale: str) -> str:
    return locale if locale in _OPENING else "en"


_TOPIC_OPENINGS = {
    "linear": (
        "Let's solve this equation together. Write your working step by step, one equation at a "
        "time, and I'll follow your reasoning. What is your first step?"
    ),
    "fractions": (
        "An equation with fractions. Write each step as an equation, and I'll follow your "
        "reasoning. What will you do first with the denominators?"
    ),
    "expressions": (
        "Let's expand and simplify this expression. Write each step, using = between the forms "
        "if you like. Where will you start?"
    ),
}


def topic_opening(topic_id: str) -> str:
    return _TOPIC_OPENINGS.get(topic_id, _TOPIC_OPENINGS["linear"])


def opening(locale: str) -> str:
    return _OPENING[_language(locale)]


def custom_opening(locale: str) -> str:
    return _CUSTOM_OPENING[_language(locale)]


def safe_hint(locale: str, directive: str, mistake) -> str:
    language = _language(locale)
    if directive in _DIRECTIVE_HINTS:
        return _DIRECTIVE_HINTS[directive][language]
    kind = getattr(mistake, "type", None)
    return _HINTS_BY_MISTAKE[language].get(kind, _GENERAL_HINT[language])
