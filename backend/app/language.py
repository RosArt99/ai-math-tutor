"""Checks on the wording of tutor replies that prompts alone did not reliably enforce."""

from __future__ import annotations

import re

# Past-tense forms that state the student's gender in Russian ("ты нашёл" / "ты нашла"). The
# list is limited to verbs a maths teacher actually uses, so ordinary words never match.
_MASCULINE = [
    "нашёл",
    "нашел",
    "перенёс",
    "перенес",
    "привёл",
    "привел",
    "вычел",
    "написал",
    "записал",
    "посчитал",
    "пересчитал",
    "подсчитал",
    "вычислил",
    "решил",
    "сделал",
    "получил",
    "умножил",
    "разделил",
    "раскрыл",
    "сложил",
    "вычитал",
    "забыл",
    "ошибся",
    "допустил",
    "подставил",
    "проверил",
    "упростил",
    "ответил",
    "понял",
    "сократил",
    "выбрал",
    "перенёс",
    "заметил",
    "исправил",
    "справился",
    "запутался",
    "угадал",
    "применил",
    "использовал",
    "перепутал",
    "потерял",
    "оставил",
    "собрал",
    "выразил",
    "довёл",
    "довел",
    "старался",
    "постарался",
]
_FEMININE = [
    "нашла",
    "перенесла",
    "привела",
    "вычла",
    "довела",
    "ошиблась",
    *[word + "а" for word in _MASCULINE if word.endswith("л")],
    *[word[:-3] + "лась" for word in _MASCULINE if word.endswith("лся")],
]
_GENDERED = re.compile(
    r"(?<![а-яёА-ЯЁ])(?P<word>"
    + "|".join(sorted(set(_MASCULINE + _FEMININE), key=len, reverse=True))
    + r")(?![а-яёА-ЯЁ])",
    re.IGNORECASE,
)
# "Я решил" is the teacher talking about themself, which does not concern the student.
_FIRST_PERSON = re.compile(r"(?:^|[^а-яё])(?:я|мы)\s+(?:\S+\s+)?$", re.IGNORECASE)


def gendered_word(reply: str, locale: str) -> str | None:
    """A word in a Russian reply that assumes the student's gender, if there is one."""
    if locale != "ru":
        return None
    for match in _GENDERED.finditer(reply):
        if _FIRST_PERSON.search(reply[max(0, match.start() - 20) : match.start()]):
            continue
        return match.group("word")
    return None
