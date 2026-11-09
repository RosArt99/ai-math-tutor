import pytest

from app.language import gendered_word


@pytest.mark.parametrize(
    "reply",
    [
        "Перенёс слагаемые — хорошо.",
        "Ты верно нашёл x.",
        "Ты нашла ошибку!",
        "В строке, которую ты написал, есть ошибка.",
        "Ты запуталась, давай медленнее.",
        "Кажется, ты ошиблась в знаке.",
    ],
)
def test_detects_gendered_forms(reply):
    assert gendered_word(reply, "ru") is not None


@pytest.mark.parametrize(
    "reply",
    [
        "Слагаемые перенесены. Что дальше?",
        "В этой строке есть ошибка. Пересчитай, пожалуйста.",
        "Я не давал ответ — посмотри ещё раз.",
        "Мы решили это вместе.",
        "Это материал из учебника, сигнал понятен.",
        "Что получается, если раскрыть скобки?",
    ],
)
def test_ignores_neutral_and_first_person_text(reply):
    assert gendered_word(reply, "ru") is None


def test_only_applies_to_russian():
    assert gendered_word("Перенёс слагаемые", "en") is None
