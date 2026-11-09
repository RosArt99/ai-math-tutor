"""Deterministic mathematics engine: the tutor's source of mathematical truth.

Language models are good at conversation and unreliable at bookkeeping, so everything
checkable is checked here and handed to the model as fact.

Student input is parsed by a small hand-written parser instead of ``sympy.parse_expr``:
``parse_expr`` evaluates its input with ``eval``, and SymPy distributes ``3*(x+4)`` the moment
the expression is built. Keeping our own syntax tree is safe on untrusted input and preserves
the structure needed to recognise *how* a student went wrong, not merely that they did.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import combinations

import sympy as sp

X = sp.Symbol("x")

MAX_INPUT_LENGTH = 200
MAX_NUMBER_DIGITS = 12
MAX_EXPONENT = 6
MAX_NESTING = 20
MAX_DEGREE = 2


class ParseError(ValueError):
    """The text is not a supported mathematical expression."""


# --------------------------------------------------------------------------- syntax tree


@dataclass(frozen=True)
class Num:
    value: Fraction


@dataclass(frozen=True)
class Var:
    pass


@dataclass(frozen=True)
class Neg:
    operand: Node


@dataclass(frozen=True)
class Add:
    left: Node
    right: Node


@dataclass(frozen=True)
class Sub:
    left: Node
    right: Node


@dataclass(frozen=True)
class Mul:
    left: Node
    right: Node


@dataclass(frozen=True)
class Div:
    left: Node
    right: Node


@dataclass(frozen=True)
class Pow:
    base: Node
    exponent: int


Node = Num | Var | Neg | Add | Sub | Mul | Div | Pow


def contains_x(node: Node) -> bool:
    match node:
        case Var():
            return True
        case Num():
            return False
        case Neg(operand):
            return contains_x(operand)
        case Pow(base, _):
            return contains_x(base)
        case Add(left, right) | Sub(left, right) | Mul(left, right) | Div(left, right):
            return contains_x(left) or contains_x(right)
    raise TypeError(node)


def to_sympy(node: Node) -> sp.Expr:
    match node:
        case Num(value):
            return sp.Rational(value.numerator, value.denominator)
        case Var():
            return X
        case Neg(operand):
            return -to_sympy(operand)
        case Add(left, right):
            return to_sympy(left) + to_sympy(right)
        case Sub(left, right):
            return to_sympy(left) - to_sympy(right)
        case Mul(left, right):
            return to_sympy(left) * to_sympy(right)
        case Div(left, right):
            return to_sympy(left) / to_sympy(right)
        case Pow(base, exponent):
            return to_sympy(base) ** exponent
    raise TypeError(node)


# ------------------------------------------------------------------------------- parsing

_TOKEN = re.compile(r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<var>x)|(?P<op>[-+*/^()]))")
_COLON_DIVISION = re.compile(r"(?<=[\dx)])\s*:\s*(?=[\dx(])")
_SUPERSCRIPTS = {"²": "^2", "³": "^3"}


def normalize_math(text: str) -> str:
    """Map the many ways of typing maths (including Cyrillic 'х') onto one alphabet."""
    result = text.strip()
    for source, target in (
        ("х", "x"),
        ("Х", "x"),
        ("X", "x"),
        ("−", "-"),
        ("–", "-"),
        ("×", "*"),
        ("·", "*"),
        ("∙", "*"),
        ("÷", "/"),
        ("**", "^"),
        (" ", " "),
    ):
        result = result.replace(source, target)
    for source, target in _SUPERSCRIPTS.items():
        result = result.replace(source, target)
    result = re.sub(r"(?<=\d),(?=\d)", ".", result)
    return _COLON_DIVISION.sub("/", result)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        if text[position:].isspace():
            break
        match = _TOKEN.match(text, position)
        if match is None:
            raise ParseError(f"Unexpected character: {text[position]!r}")
        kind = match.lastgroup
        assert kind is not None
        tokens.append((kind, match.group(kind)))
        position = match.end()
    return tokens


class _Parser:
    """expr := term (('+'|'-') term)* ; term := unary (('*'|'/'|implicit) unary)*"""

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.position = 0
        self.depth = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def accept(self, op: str) -> bool:
        if self.peek() == ("op", op):
            self.position += 1
            return True
        return False

    def parse(self) -> Node:
        if not self.tokens:
            raise ParseError("Empty expression")
        node = self.expression()
        if self.peek() is not None:
            raise ParseError(f"Unexpected token: {self.peek()[1]!r}")  # type: ignore[index]
        return node

    def expression(self) -> Node:
        node = self.term()
        while True:
            if self.accept("+"):
                node = Add(node, self.term())
            elif self.accept("-"):
                node = Sub(node, self.term())
            else:
                return node

    def term(self) -> Node:
        node = self.unary()
        while True:
            if self.accept("*"):
                node = Mul(node, self.unary())
            elif self.accept("/"):
                divisor = self.unary()
                if contains_x(divisor):
                    raise ParseError("Division by an expression containing x is not supported")
                if to_sympy(divisor) == 0:
                    raise ParseError("Division by zero")
                node = Div(node, divisor)
            elif self._implicit_multiplication_follows(node):
                node = Mul(node, self.power())
            else:
                return node

    def _implicit_multiplication_follows(self, previous: Node) -> bool:
        token = self.peek()
        if token is None:
            return False
        if token[0] == "num":
            # "x2" and "(x+1)2" are more likely typos than products; refuse to guess.
            if isinstance(previous, Num):
                return False
            raise ParseError("A number cannot directly follow x or a bracket")
        return token[0] == "var" or token == ("op", "(")

    def unary(self) -> Node:
        if self.accept("-"):
            return Neg(self.unary())
        if self.accept("+"):
            return self.unary()
        return self.power()

    def power(self) -> Node:
        base = self.atom()
        if not self.accept("^"):
            return base
        token = self.peek()
        if token is None or token[0] != "num" or "." in token[1]:
            raise ParseError("Only whole-number exponents are supported")
        exponent = int(token[1])
        if exponent > MAX_EXPONENT:
            raise ParseError("Exponent is too large")
        self.position += 1
        return Pow(base, exponent)

    def atom(self) -> Node:
        token = self.peek()
        if token is None:
            raise ParseError("Expression ends unexpectedly")
        kind, value = token
        if kind == "num":
            if len(value) > MAX_NUMBER_DIGITS:
                raise ParseError("Number is too long")
            self.position += 1
            return Num(Fraction(value))
        if kind == "var":
            self.position += 1
            return Var()
        if self.accept("("):
            self.depth += 1
            if self.depth > MAX_NESTING:
                raise ParseError("Brackets are nested too deeply")
            node = self.expression()
            if not self.accept(")"):
                raise ParseError("Missing closing bracket")
            self.depth -= 1
            return node
        raise ParseError(f"Unexpected token: {value!r}")


def parse_expression(text: str) -> Node:
    if len(text) > MAX_INPUT_LENGTH:
        raise ParseError("Expression is too long")
    return _Parser(_tokenize(normalize_math(text))).parse()


# ----------------------------------------------------------------------------- rendering


def _precedence(node: Node) -> int:
    match node:
        case Add() | Sub():
            return 1
        case Mul() | Div() | Neg():
            return 2
        case Pow():
            return 3
    return 4


def _format_number(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    decimal = float(value)
    return f"{decimal:g}" if Fraction(f"{decimal:g}") == value else f"{value}"


def to_text(node: Node) -> str:
    """Render a tree the way it would be written by hand: ``3(x + 4)``."""

    def wrap(child: Node, minimum: int) -> str:
        rendered = to_text(child)
        return f"({rendered})" if _precedence(child) < minimum else rendered

    match node:
        case Num(value):
            return _format_number(value)
        case Var():
            return "x"
        case Neg(operand):
            return f"-{wrap(operand, 2)}"
        case Add(left, right):
            return f"{to_text(left)} + {wrap(right, 2)}"
        case Sub(left, right):
            return f"{to_text(left)} - {wrap(right, 2)}"
        case Mul(left, right):
            left_text = wrap(left, 2)
            right_text = wrap(right, 3)
            if isinstance(left, Num | Neg) and not isinstance(right, Num | Neg):
                return f"{left_text}{right_text}"
            if isinstance(right, Var) or right_text.startswith("("):
                if not isinstance(right, Num):
                    return f"{left_text}{right_text}"
            return f"{left_text} * {right_text}"
        case Div(left, right):
            return f"{wrap(left, 2)} / {wrap(right, 3)}"
        case Pow(base, exponent):
            return f"{wrap(base, 4)}^{exponent}"
    raise TypeError(node)


def to_latex(node: Node) -> str:
    def wrap(child: Node, minimum: int) -> str:
        rendered = to_latex(child)
        return f"({rendered})" if _precedence(child) < minimum else rendered

    match node:
        case Num(value):
            if value.denominator == 1:
                return str(value.numerator)
            sign = "-" if value < 0 else ""
            return rf"{sign}\frac{{{abs(value.numerator)}}}{{{value.denominator}}}"
        case Var():
            return "x"
        case Neg(operand):
            return f"-{wrap(operand, 2)}"
        case Add(left, right):
            return f"{to_latex(left)} + {wrap(right, 2)}"
        case Sub(left, right):
            return f"{to_latex(left)} - {wrap(right, 2)}"
        case Mul(left, right):
            left_text = wrap(left, 2)
            right_text = wrap(right, 3)
            if isinstance(right, Num):
                return rf"{left_text} \cdot {right_text}"
            return f"{left_text}{right_text}"
        case Div(left, right):
            return rf"\frac{{{to_latex(left)}}}{{{to_latex(right)}}}"
        case Pow(base, exponent):
            return f"{wrap(base, 4)}^{{{exponent}}}"
    raise TypeError(node)


def format_expr(expr: sp.Expr) -> str:
    """Render a SymPy value compactly, e.g. ``3x + 12``."""
    return sp.sstr(sp.expand(expr)).replace("**", "^").replace("*", "")


# ----------------------------------------------------------------------------- equations


@dataclass(frozen=True)
class SolutionSet:
    kind: str  # "finite" | "all" | "none" | "unsupported"
    values: tuple[sp.Expr, ...] = ()

    def same_as(self, other: SolutionSet) -> bool:
        if self.kind != other.kind:
            return False
        if self.kind != "finite":
            return self.kind != "unsupported"
        if len(self.values) != len(other.values):
            return False
        return all(any(sp.simplify(a - b) == 0 for b in other.values) for a in self.values)


@dataclass(frozen=True)
class Equation:
    left: Node
    right: Node

    @property
    def text(self) -> str:
        return f"{to_text(self.left)} = {to_text(self.right)}"

    @property
    def latex(self) -> str:
        return f"{to_latex(self.left)} = {to_latex(self.right)}"

    @property
    def has_x(self) -> bool:
        return contains_x(self.left) or contains_x(self.right)

    @property
    def difference(self) -> sp.Expr:
        return sp.expand(to_sympy(self.left) - to_sympy(self.right))

    def solutions(self) -> SolutionSet:
        difference = self.difference
        if not difference.has(X):
            return SolutionSet("all" if difference == 0 else "none")
        polynomial = sp.Poly(difference, X)
        if polynomial.degree() > MAX_DEGREE:
            return SolutionSet("unsupported")
        roots = [root for root in sp.roots(polynomial) if root.is_real]
        return (
            SolutionSet("finite", tuple(sorted(roots, key=float))) if roots else SolutionSet("none")
        )

    def solved_value(self) -> sp.Expr | None:
        """The value of x when the equation is written in the final form ``x = c``."""
        for variable_side, value_side in ((self.left, self.right), (self.right, self.left)):
            if isinstance(variable_side, Var) and not contains_x(value_side):
                return to_sympy(value_side)
        return None

    def same_form_as(self, other: Equation) -> bool:
        """True when both are the same statement, ignoring spacing and swapped sides."""
        return self.text == other.text or (
            to_text(self.left) == to_text(other.right)
            and to_text(self.right) == to_text(other.left)
        )


def parse_equation(text: str) -> Equation:
    normalized = normalize_math(text)
    parts = normalized.split("=")
    if len(parts) != 2:
        raise ParseError("An equation needs exactly one '=' sign")
    return Equation(parse_expression(parts[0]), parse_expression(parts[1]))


def substitute(equation: Equation, value: sp.Expr) -> tuple[sp.Expr, sp.Expr]:
    left = sp.simplify(to_sympy(equation.left).subs(X, value))
    right = sp.simplify(to_sympy(equation.right).subs(X, value))
    return left, right


# -------------------------------------------------------------------- finding maths in prose

_VARIABLE = r"(?<![^\W\d_])[xXхХ](?![^\W\d_])"
_MATH_RUN = re.compile(
    r"(?:\d+(?:[.,]\d+)?|" + _VARIABLE + r"|\*\*|[-+−–*/×·∙÷^()²³=:]|[ \t\u00a0])+"
)
_LATEX_FRACTION = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_EQUALS_WORDS = re.compile(r"(?i)\s+(?:равно|равна|равен|равны|равняется|equals|is equal to)\s+")
_NUMBER = re.compile(r"(?<![\d.,])-?\d+(?:[.,]\d+)?")


def _strip_latex(text: str) -> str:
    result = text
    while _LATEX_FRACTION.search(result):
        result = _LATEX_FRACTION.sub(r"((\1)/(\2))", result)
    for source, target in (
        ("$", " "),
        (r"\(", " "),
        (r"\)", " "),
        (r"\[", " "),
        (r"\]", " "),
        (r"\cdot", "*"),
        (r"\times", "*"),
        (r"\div", "/"),
        (r"\left", ""),
        (r"\right", ""),
        (r"\,", " "),
        (r"\;", " "),
        (r"\ ", " "),
    ):
        result = result.replace(source, target)
    return re.sub(r"\^\{(\d+)\}", r"^\1", result)


def _math_runs(text: str) -> list[str]:
    cleaned = _strip_latex(text)
    # Implication arrows and em dashes separate statements; they are never minus signs.
    cleaned = re.sub(r"=>|⇒|⇔|->|→|—|<=>", "\n", cleaned)
    cleaned = _EQUALS_WORDS.sub(" = ", cleaned)
    # A list marker at the start of a line is not part of the mathematics.
    cleaned = re.sub(r"(?m)^\s*(?:[-•*]\s+|\d+[.)]\s+)", "", cleaned)
    return [match.group(0).strip(" \t\u00a0:") for match in _MATH_RUN.finditer(cleaned)]


def extract_equations(text: str) -> list[Equation]:
    """Find every parsable equation in free text, in reading order."""
    equations: list[Equation] = []
    for run in _math_runs(text):
        if run.count("=") != 1:
            continue
        try:
            equations.append(parse_equation(run))
        except ParseError:
            continue
    return equations


def extract_expressions(text: str) -> list[Node]:
    """Find standalone expressions containing x; used to catch leaked intermediate results."""
    expressions: list[Node] = []
    for run in _math_runs(text):
        for part in run.split("="):
            try:
                node = parse_expression(part)
            except ParseError:
                continue
            if contains_x(node):
                expressions.append(node)
    return expressions


def message_contains_number(text: str, value: sp.Expr) -> bool:
    """Whether a message states a number equal to ``value``."""
    for match in _NUMBER.finditer(_strip_latex(text)):
        try:
            if sp.Rational(match.group(0).replace(",", ".")) == value:
                return True
        except (TypeError, ValueError):
            continue
    return False


def count_number(text: str, value: sp.Expr) -> int:
    """How many times a message states a number equal to ``value``."""
    count = 0
    for match in _NUMBER.finditer(_strip_latex(text)):
        try:
            count += sp.Rational(match.group(0).replace(",", ".")) == value
        except (TypeError, ValueError):
            continue
    return count


def parse_bare_number(text: str) -> sp.Expr | None:
    """Interpret a message that is nothing but a number, e.g. ``7`` or ``-3.5``."""
    candidate = normalize_math(_strip_latex(text)).rstrip(".!").strip()
    if not re.fullmatch(r"-?\d{1,12}(?:\.\d{1,8})?", candidate):
        return None
    return sp.Rational(candidate)


# ------------------------------------------------------------------------- error diagnosis


@dataclass(frozen=True)
class Diagnosis:
    """A recognised reasoning error, with evidence the tutor can build a question around."""

    type: str
    skill: str
    evidence: dict[str, str] = field(default_factory=dict)


SKILL_FOR_MISTAKE = {
    "distribution_partial": "distribution",
    "distribution_sign": "distribution",
    "transposition_sign": "transposition",
    "one_sided_operation": "equality_balance",
    "inverse_operation": "isolating_variable",
    "inverted_division": "isolating_variable",
    "arithmetic_slip": "combining_terms",
    "false_equality": "checking",
    "lcd_not_all_terms": "clearing_fractions",
    "lcd_numerator_partial": "clearing_fractions",
    "square_missing_middle": "special_products",
    "square_middle_not_doubled": "special_products",
    "square_sign": "special_products",
    "conceptual": "equation_reasoning",
    "unknown": "equation_reasoning",
}


def _proportional(a: sp.Expr, b: sp.Expr) -> bool:
    """Two differences describe the same equation if one is a non-zero multiple of the other."""
    a, b = sp.expand(a), sp.expand(b)
    if a == 0 or b == 0:
        return a == b
    ratio = sp.cancel(a / b)
    return bool(ratio.is_number) and ratio != 0


def _signed_terms(node: Node, sign: int = 1) -> list[tuple[int, Node]]:
    match node:
        case Add(left, right):
            return _signed_terms(left, sign) + _signed_terms(right, sign)
        case Sub(left, right):
            return _signed_terms(left, sign) + _signed_terms(right, -sign)
        case Neg(operand):
            return _signed_terms(operand, -sign)
    return [(sign, node)]


@dataclass(frozen=True)
class _WrongExpansion:
    side_value: sp.Expr
    kind: str
    coefficient: sp.Expr
    bracket: str
    correct: sp.Expr
    wrong: sp.Expr
    untouched: str


def _sign_of(value: sp.Expr) -> int:
    return -1 if value.is_number and value < 0 else 1


def _site_variants(coefficient: sp.Expr, inner: Node) -> Iterator[_WrongExpansion]:
    """All the ways a student plausibly mis-expands ``coefficient * (inner)``."""
    match inner:
        case Neg(operand):
            yield from _site_variants(-coefficient, operand)
            return
        case Mul(left, right) if not contains_x(left):
            yield from _site_variants(coefficient * to_sympy(left), right)
            return
        case Mul(left, right) if not contains_x(right):
            yield from _site_variants(coefficient * to_sympy(right), left)
            return
        case Div(left, right):
            yield from _site_variants(coefficient / to_sympy(right), left)
            return
        case Mul(Var(), right):
            yield from _site_variants(coefficient * X, right)
            return
        case Pow(base, 2) if isinstance(base, Add | Sub):
            for variant in _square_variants(base):
                yield _scaled(variant, coefficient)
            return
        case Add() | Sub():
            pass
        case _:
            return

    terms = [(sign, to_sympy(term)) for sign, term in _signed_terms(inner)]
    first = terms[0][0] * terms[0][1]
    rest = [sign * value for sign, value in terms[1:]]
    correct = sp.expand(coefficient * to_sympy(inner))
    sign = _sign_of(coefficient)
    candidates: list[tuple[str, sp.Expr]] = []
    if coefficient in (1, -1):
        # -(x + 3) has nothing to multiply by, so the only error is the sign of later terms.
        candidates.append(("distribution_sign", coefficient * first + sum(rest)))
    else:
        candidates.append(("distribution_partial", coefficient * first + sum(rest)))
        candidates.append(("distribution_partial", coefficient * first + sign * sum(rest)))
        candidates.append(("distribution_sign", coefficient * first - coefficient * sum(rest)))
    bracket = to_text(inner)
    untouched = format_expr(sum(rest))
    seen: set[sp.Expr] = set()
    for kind, wrong in candidates:
        wrong = sp.expand(wrong)
        if wrong == correct or wrong in seen:
            continue
        seen.add(wrong)
        yield _WrongExpansion(wrong, kind, coefficient, bracket, correct, wrong, untouched)

    for term_sign, term in _signed_terms(inner):
        for variant in _wrong_expansions(term):
            yield _scaled(variant, coefficient * term_sign)


def _scaled(variant: _WrongExpansion, factor: sp.Expr) -> _WrongExpansion:
    return _WrongExpansion(
        side_value=variant.side_value * factor,
        kind=variant.kind,
        coefficient=variant.coefficient,
        bracket=variant.bracket,
        correct=variant.correct,
        wrong=variant.wrong,
        untouched=variant.untouched,
    )


def _shifted(variant: _WrongExpansion, offset: sp.Expr) -> _WrongExpansion:
    return _WrongExpansion(
        side_value=variant.side_value + offset,
        kind=variant.kind,
        coefficient=variant.coefficient,
        bracket=variant.bracket,
        correct=variant.correct,
        wrong=variant.wrong,
        untouched=variant.untouched,
    )


def _square_variants(base: Node) -> Iterator[_WrongExpansion]:
    """Classic errors with (a ± b)²: the middle term dropped, not doubled, or with a wrong sign."""
    terms = [sign * to_sympy(term) for sign, term in _signed_terms(base)]
    if len(terms) != 2:
        return
    a, b = terms
    correct = sp.expand((a + b) ** 2)
    candidates = [
        ("square_missing_middle", a**2 + b**2),
        ("square_missing_middle", a**2 - b**2),
        ("square_middle_not_doubled", a**2 + a * b + b**2),
        ("square_sign", a**2 - 2 * a * b + b**2),
    ]
    seen: set[sp.Expr] = set()
    for kind, wrong in candidates:
        wrong = sp.expand(wrong)
        if wrong == correct or wrong in seen:
            continue
        seen.add(wrong)
        yield _WrongExpansion(
            wrong,
            kind,
            sp.Integer(1),
            f"({to_text(base)})^2",
            correct,
            wrong,
            format_expr(2 * a * b),
        )


def _wrong_expansions(node: Node) -> Iterator[_WrongExpansion]:
    """Each yielded value is the whole of ``node`` with exactly one bracket mis-expanded."""
    match node:
        case Add(left, right):
            for variant in _wrong_expansions(left):
                yield _shifted(variant, to_sympy(right))
            for variant in _wrong_expansions(right):
                yield _shifted(variant, to_sympy(left))
        case Sub(left, right):
            for variant in _wrong_expansions(left):
                yield _shifted(variant, -to_sympy(right))
            for variant in _wrong_expansions(Neg(right)):
                yield _shifted(variant, to_sympy(left))
        case Neg(operand):
            yield from _site_variants(sp.Integer(-1), operand)
        case Mul(left, right) if not contains_x(left):
            yield from _site_variants(to_sympy(left), right)
        case Mul(left, right) if not contains_x(right):
            yield from _site_variants(to_sympy(right), left)
        case Mul(Var(), right):
            yield from _site_variants(X, right)
        case Div(left, right):
            yield from _site_variants(1 / to_sympy(right), left)
        case Pow(base, 2) if isinstance(base, Add | Sub):
            yield from _square_variants(base)


def _diagnose_distribution(previous: Equation, student_diff: sp.Expr) -> Diagnosis | None:
    left, right = to_sympy(previous.left), to_sympy(previous.right)
    variants = [
        (variant, variant.side_value - right) for variant in _wrong_expansions(previous.left)
    ]
    variants += [
        (variant, left - variant.side_value) for variant in _wrong_expansions(previous.right)
    ]
    for variant, difference in variants:
        if _proportional(student_diff, difference):
            if variant.kind.startswith("square"):
                return _square_diagnosis(variant)
            return Diagnosis(
                type=variant.kind,
                skill=SKILL_FOR_MISTAKE[variant.kind],
                evidence={
                    "bracket": f"{format_expr(variant.coefficient)}({variant.bracket})",
                    "factor": format_expr(variant.coefficient),
                    "correct_expansion": format_expr(variant.correct),
                    "student_expansion": format_expr(variant.wrong),
                    "terms_mishandled": variant.untouched,
                },
            )
    return None


def _square_diagnosis(variant: _WrongExpansion) -> Diagnosis:
    return Diagnosis(
        type=variant.kind,
        skill=SKILL_FOR_MISTAKE[variant.kind],
        evidence={
            "square": variant.bracket,
            "correct_expansion": format_expr(variant.correct),
            "student_expansion": format_expr(variant.wrong),
            "middle_term": variant.untouched,
        },
    )


def _denominators(node: Node) -> list[int]:
    match node:
        case Div(left, right):
            value = to_sympy(right)
            found = [int(abs(value))] if value.is_integer else []
            return found + _denominators(left)
        case Add(left, right) | Sub(left, right) | Mul(left, right):
            return _denominators(left) + _denominators(right)
        case Neg(operand):
            return _denominators(operand)
    return []


def _diagnose_clearing(previous: Equation, student_diff: sp.Expr) -> Diagnosis | None:
    """Multiplying by the common denominator but missing a term, or half a numerator."""
    denominators = _denominators(previous.left) + _denominators(previous.right)
    if not denominators:
        return None
    k = sp.ilcm(*denominators) if len(denominators) > 1 else sp.Integer(denominators[0])
    options: list[list[tuple[str, sp.Expr]]] = []
    for side_sign, node in ((1, previous.left), (-1, previous.right)):
        for sign, term in _signed_terms(node):
            value = side_sign * sign * to_sympy(term)
            choices = [("ok", sp.expand(k * value)), ("lcd_not_all_terms", value)]
            if isinstance(term, Div) and isinstance(term.left, Add | Sub):
                inner = [s * to_sympy(t) for s, t in _signed_terms(term.left)]
                factor = side_sign * sign * k / to_sympy(term.right)
                choices.append(
                    ("lcd_numerator_partial", sp.expand(factor * inner[0] + sum(inner[1:])))
                )
            options.append(choices)
    if len(options) > 6:
        return None

    def combine(index: int, total: sp.Expr, kinds: tuple[str, ...]) -> Iterator[tuple]:
        if index == len(options):
            yield total, kinds
            return
        for kind, value in options[index]:
            yield from combine(index + 1, total + value, kinds + (kind,))

    for total, kinds in combine(0, sp.Integer(0), ()):
        wrong = [kind for kind in kinds if kind != "ok"]
        if not wrong or len(wrong) == len(kinds):
            continue
        if _proportional(student_diff, total):
            kind = "lcd_numerator_partial" if "lcd_numerator_partial" in wrong else wrong[0]
            return Diagnosis(
                type=kind,
                skill=SKILL_FOR_MISTAKE[kind],
                evidence={"common_denominator": str(k), "terms_missed": str(len(wrong))},
            )
    return None


def _side_terms(previous: Equation) -> list[tuple[str, sp.Expr]]:
    terms: list[tuple[str, sp.Expr]] = []
    for side, node in (("left", previous.left), ("right", previous.right)):
        expanded = sp.expand(to_sympy(node))
        if expanded != 0:
            terms += [(side, term) for term in sp.Add.make_args(expanded)]
    return terms


def _diagnose_transposition(previous: Equation, student_diff: sp.Expr) -> Diagnosis | None:
    """Moving a term across '=' without changing its sign shifts the difference by ±2t."""
    terms = _side_terms(previous)
    if len(terms) > 8:
        return None
    base = previous.difference
    for size in range(1, len(terms) + 1):
        for subset in combinations(terms, size):
            shift = sum((2 * t if side == "right" else -2 * t) for side, t in subset)
            candidate = sp.expand(base + shift)
            if candidate != 0 and _proportional(student_diff, candidate):
                return Diagnosis(
                    type="transposition_sign",
                    skill=SKILL_FOR_MISTAKE["transposition_sign"],
                    evidence={"moved_terms": ", ".join(format_expr(t) for _, t in subset)},
                )
    return None


def _diagnose_one_sided(previous: Equation, student_diff: sp.Expr) -> Diagnosis | None:
    """Removing a term from one side only shifts the difference by ±t."""
    base = previous.difference
    for side, term in _side_terms(previous):
        for candidate in (base - term, base + term):
            candidate = sp.expand(candidate)
            if candidate != 0 and _proportional(student_diff, candidate):
                return Diagnosis(
                    type="one_sided_operation",
                    skill=SKILL_FOR_MISTAKE["one_sided_operation"],
                    evidence={"term": format_expr(term), "side": side},
                )
    return None


def _diagnose_isolation(previous: Equation, student: Equation) -> Diagnosis | None:
    """ax = b wrongly finished with b - a, b + a, a * b, a / b or simply b."""
    value = student.solved_value()
    if value is None:
        return None
    for variable_side, value_side in (
        (previous.left, previous.right),
        (previous.right, previous.left),
    ):
        expanded = sp.expand(to_sympy(variable_side))
        if contains_x(value_side) or not expanded.has(X) or sp.degree(expanded, X) != 1:
            continue
        a = expanded.coeff(X, 1)
        if expanded - a * X != 0 or a in (0, 1):
            continue
        b = to_sympy(value_side)
        if b != 0 and value == a / b:
            mistake = "inverted_division"
        elif value in (b - a, b + a, a * b):
            mistake = "inverse_operation"
        elif value == b:
            mistake = "one_sided_operation"
        else:
            continue
        return Diagnosis(
            type=mistake,
            skill=SKILL_FOR_MISTAKE[mistake],
            evidence={"coefficient": format_expr(a), "right_value": format_expr(b)},
        )
    return None


def _diagnose_arithmetic(previous: Equation, student: Equation) -> Diagnosis | None:
    """One side kept unchanged and the other off by a single coefficient."""
    pairs = (
        (student.left, student.right, previous.left, previous.right),
        (student.right, student.left, previous.right, previous.left),
    )
    for student_same, student_other, previous_same, previous_other in pairs:
        if sp.expand(to_sympy(student_same) - to_sympy(previous_same)) != 0:
            continue
        delta = sp.expand(to_sympy(student_other) - to_sympy(previous_other))
        if delta != 0 and len(sp.Add.make_args(delta)) == 1:
            return Diagnosis(
                type="arithmetic_slip",
                skill=SKILL_FOR_MISTAKE["arithmetic_slip"],
                evidence={"side_written": to_text(student_other)},
            )
    return None


def diagnose(previous: Equation, student: Equation) -> Diagnosis:
    """Explain how ``student`` could have been (wrongly) derived from ``previous``."""
    student_diff = student.difference
    finders = (
        _diagnose_clearing(previous, student_diff),
        _diagnose_distribution(previous, student_diff),
        _diagnose_isolation(previous, student),
        # One-sided first: it explains the step with a single term, while a multi-term
        # transposition can match the same equation by numerical coincidence.
        _diagnose_one_sided(previous, student_diff),
        _diagnose_transposition(previous, student_diff),
        _diagnose_arithmetic(previous, student),
    )
    for diagnosis in finders:
        if diagnosis is not None:
            return diagnosis
    return Diagnosis(type="unknown", skill=SKILL_FOR_MISTAKE["unknown"])


# ------------------------------------------------------------------------- step analysis


@dataclass(frozen=True)
class StepCheck:
    """The verdict on one equation written by a student."""

    equation: Equation
    # "valid" | "restated" | "final_correct" | "invalid" | "continues_error" for equations in x;
    # "true_statement" | "false_statement" for plain arithmetic such as "3(7 + 4) = 33".
    status: str
    diagnosis: Diagnosis | None = None
    substitution: dict[str, str] | None = None
    kind: str | None = None  # how a correct step was made; see describe_step


def _has_bracket_product(equation: Equation) -> bool:
    return _has_brackets(equation.left) or _has_brackets(equation.right)


def describe_step(previous: Equation, new: Equation) -> str:
    """Name the transformation a student actually used, so the tutor never re-describes it.

    Moving terms with a sign change is taught as its own rule in the Soviet curriculum; calling
    it "subtracting from both sides" would replace the student's method with another one.
    """
    before, after = previous.difference, new.difference
    if sp.expand(after - before) == 0 or sp.expand(after + before) == 0:
        same_sides = (
            sp.expand(to_sympy(new.left) - to_sympy(previous.left)) == 0
            and sp.expand(to_sympy(new.right) - to_sympy(previous.right)) == 0
        ) or (
            sp.expand(to_sympy(new.left) - to_sympy(previous.right)) == 0
            and sp.expand(to_sympy(new.right) - to_sympy(previous.left)) == 0
        )
        if same_sides:
            if _has_bracket_product(previous) and not _has_bracket_product(new):
                return "opened_brackets"
            return "simplified_sides"
        if _has_bracket_product(previous) and not _has_bracket_product(new):
            return "opened_brackets_and_moved_terms"
        return "moved_terms"
    if before != 0:
        ratio = sp.cancel(after / before)
        if ratio.is_number and ratio != 0:
            had_fractions = _denominators(previous.left) + _denominators(previous.right)
            if had_fractions and not (_denominators(new.left) + _denominators(new.right)):
                return "cleared_fractions"
            return "divided_both_sides" if abs(ratio) < 1 else "multiplied_both_sides"
    return "other_equivalent"


def check_numeric_statements(written: list[Equation]) -> list[StepCheck]:
    """Evaluate the arithmetic a student writes, e.g. while checking by substitution."""
    checks: list[StepCheck] = []
    for equation in written:
        if equation.has_x:
            continue
        left = sp.simplify(to_sympy(equation.left))
        right = sp.simplify(to_sympy(equation.right))
        if left == right:
            checks.append(StepCheck(equation, "true_statement"))
            continue
        diagnosis = Diagnosis(
            type="false_equality",
            skill=SKILL_FOR_MISTAKE["false_equality"],
            evidence={
                "statement": equation.text,
                "left_value": format_expr(left),
                "right_value": format_expr(right),
            },
        )
        checks.append(StepCheck(equation, "false_statement", diagnosis))
    return checks


def merge_in_order(
    written: list[Equation], equation_checks: list[StepCheck], numeric_checks: list[StepCheck]
) -> list[StepCheck]:
    """Interleave both kinds of check in the order the student wrote them."""
    equations, numbers = iter(equation_checks), iter(numeric_checks)
    merged = []
    for equation in written:
        source = equations if equation.has_x else numbers
        item = next(source, None)
        if item is not None:
            merged.append(item)
    return merged


def substitution_report(original: Equation, value: sp.Expr) -> dict[str, str]:
    left, right = substitute(original, value)
    return {"x": format_expr(value), "left": format_expr(left), "right": format_expr(right)}


def check_steps(
    original: Equation,
    last_valid: Equation,
    wrong_equation: Equation | None,
    written: list[Equation],
) -> list[StepCheck]:
    """Follow a student's equations in order, stopping at the first new error."""
    truth = original.solutions()
    current = last_valid
    wrong = wrong_equation
    checks: list[StepCheck] = []
    for equation in written:
        if not equation.has_x:
            continue
        solutions = equation.solutions()
        value = equation.solved_value()
        report = substitution_report(original, value) if value is not None else None
        if truth.same_as(solutions) and truth.kind != "unsupported":
            if equation.same_form_as(current):
                status = "restated"
            elif value is not None:
                status = "final_correct"
            else:
                status = "valid"
            kind = None if status == "restated" else describe_step(current, equation)
            checks.append(StepCheck(equation, status, substitution=report, kind=kind))
            current = equation
            wrong = None
            continue
        if wrong is not None and (
            equation.same_form_as(wrong) or solutions.same_as(wrong.solutions())
        ):
            checks.append(StepCheck(equation, "continues_error", substitution=report))
            wrong = equation
            continue
        diagnosis = diagnose(current, equation)
        if diagnosis.type == "unknown" and current is not original:
            fallback = diagnose(original, equation)
            diagnosis = fallback if fallback.type != "unknown" else diagnosis
        checks.append(StepCheck(equation, "invalid", diagnosis, report))
        wrong = equation
    return checks


# ------------------------------------------------------------------------- reveal detection


def _distribution_results(equation: Equation) -> list[sp.Expr]:
    """Correct expansions of each bracket: the intermediate result students must find."""

    def walk(node: Node) -> Iterator[sp.Expr]:
        match node:
            case Add(left, right) | Sub(left, right):
                yield from walk(left)
                yield from walk(right)
            case Neg(operand):
                if isinstance(operand, Add | Sub):
                    yield sp.expand(-to_sympy(operand))
                yield from walk(operand)
            case Mul(left, right):
                if isinstance(left, Add | Sub) or isinstance(right, Add | Sub):
                    yield sp.expand(to_sympy(node))
                yield from walk(left)
                yield from walk(right)
            case Div(left, _):
                if isinstance(left, Add | Sub):
                    yield sp.expand(to_sympy(node))
                yield from walk(left)
            case Pow(base, _):
                if isinstance(base, Add | Sub):
                    yield sp.expand(to_sympy(node))
                yield from walk(base)

    return list(walk(equation.left)) + list(walk(equation.right))


def find_reveal(
    reply: str,
    original: Equation,
    last_valid: Equation,
    student_equations: list[Equation],
    student_texts: list[str] | None = None,
) -> str | None:
    """Return the fragment if a tutor reply gives away what the student should work out."""
    truth = original.solutions()
    allowed = [original, last_valid, *student_equations]
    for equation in extract_equations(reply):
        if not equation.has_x or any(equation.same_form_as(known) for known in allowed):
            continue
        if truth.kind != "unsupported" and truth.same_as(equation.solutions()):
            return equation.text

    # A bare final answer ("the answer is 7") or a bracket's expansion ("3x + 12").
    student_side_values = [
        sp.expand(to_sympy(side))
        for known in student_equations
        for side in (known.left, known.right)
    ]
    secret = [
        result
        for equation in (original, last_valid)
        for result in _distribution_results(equation)
        if result not in student_side_values
    ]
    for node in extract_expressions(reply):
        if _has_brackets(node):
            continue
        value = sp.expand(to_sympy(node))
        if any(value == result for result in secret):
            return to_text(node)

    # A bare number can give the game away too: "so the 4 must become 12", "x is 7".
    secret_numbers = {abs(value) for value in truth.values if value.is_Rational}
    secret_numbers |= {abs(result.as_coeff_Add()[0]) for result in secret}
    known_text = " ".join([original.text, last_valid.text, *(student_texts or [])])
    known_text += " " + " ".join(known.text for known in student_equations)
    return find_secret_number(reply, secret_numbers, known_text)


def find_secret_number(reply: str, secrets: set, known_text: str) -> str | None:
    """Return a number from ``secrets`` stated in ``reply`` that ``known_text`` doesn't contain."""
    hidden = {
        abs(number)
        for number in secrets
        if abs(number) not in (0, 1)
        and not message_contains_number(known_text, number)
        and not message_contains_number(known_text, -number)
    }
    for match in _NUMBER.finditer(_strip_latex(reply)):
        try:
            number = abs(sp.Rational(match.group(0).replace(",", ".")))
        except (TypeError, ValueError):
            continue
        if number in hidden:
            return match.group(0)
    return None


def false_statement_values(checks: list[StepCheck]) -> set:
    """Correct values of the computed sides of a student's false equalities, e.g. 20 in 4·5 = 25."""
    values = set()
    for check in checks:
        if check.status != "false_statement":
            continue
        for side in (check.equation.left, check.equation.right):
            if not isinstance(side, Num):
                values.add(sp.simplify(to_sympy(side)))
    return values


def _has_brackets(node: Node) -> bool:
    match node:
        case Mul(left, right):
            return (
                isinstance(left, Add | Sub)
                or isinstance(right, Add | Sub)
                or _has_brackets(left)
                or _has_brackets(right)
            )
        case Add(left, right) | Sub(left, right) | Div(left, right):
            return _has_brackets(left) or _has_brackets(right)
        case Neg(operand):
            return isinstance(operand, Add | Sub) or _has_brackets(operand)
        case Pow(base, _):
            return isinstance(base, Add | Sub) or _has_brackets(base)
    return False


# ------------------------------------------------------------------------- problem authoring

SKILLS = ("distribution", "transposition", "equality_balance", "isolating_variable")
MAX_PROBLEM_MAGNITUDE = 60


def _has_distribution_site(node: Node) -> bool:
    match node:
        case Mul(left, right):
            has_site = (
                not contains_x(left)
                and isinstance(right, Add | Sub)
                and abs(to_sympy(left)) >= 2
                and contains_x(right)
            ) or (
                not contains_x(right)
                and isinstance(left, Add | Sub)
                and abs(to_sympy(right)) >= 2
                and contains_x(left)
            )
            return has_site or _has_distribution_site(left) or _has_distribution_site(right)
        case Add(left, right) | Sub(left, right):
            return _has_distribution_site(left) or _has_distribution_site(right)
        case Neg(operand):
            return _has_distribution_site(operand)
    return False


def problem_rejection_reason(
    text: str, skill: str, previous: list[str] | None = None
) -> str | None:
    """Explain why a proposed practice problem is unusable, or return None if it is sound."""
    try:
        equation = parse_equation(text)
    except ParseError as error:
        return f"cannot parse: {error}"
    if not equation.has_x:
        return "does not contain x"
    difference = equation.difference
    if not difference.has(X) or sp.Poly(difference, X).degree() != 1:
        return "is not a linear equation with one solution"
    solutions = equation.solutions()
    if solutions.kind != "finite" or len(solutions.values) != 1:
        return "does not have exactly one solution"
    root = solutions.values[0]
    if not root.is_integer or abs(root) > MAX_PROBLEM_MAGNITUDE:
        return "the solution must be a reasonably small whole number"
    if equation.solved_value() is not None:
        return "is already solved"
    if skill == "distribution" and not (
        _has_distribution_site(equation.left) or _has_distribution_site(equation.right)
    ):
        return "must contain a bracket multiplied by a number"
    if skill == "transposition" and not (contains_x(equation.left) and contains_x(equation.right)):
        return "must have x on both sides"
    for known in previous or []:
        try:
            if equation.same_form_as(parse_equation(known)):
                return "repeats an earlier problem"
        except ParseError:
            continue
    return None


def _join(*terms: int | str) -> str:
    """Build ``3x - 4`` from signed terms, dropping zeros."""
    text = ""
    for term in terms:
        if term == 0:
            continue
        value = str(term)
        if not text:
            text = value
        elif value.startswith("-"):
            text += f" - {value[1:]}"
        else:
            text += f" + {value}"
    return text or "0"


def _x_term(coefficient: int) -> str | int:
    if coefficient == 0:
        return 0
    if coefficient == 1:
        return "x"
    if coefficient == -1:
        return "-x"
    return f"{coefficient}x"


def _generate_distribution(rng: random.Random, difficulty: int) -> str:
    a = rng.randint(2, 6 if difficulty < 3 else 9)
    b = rng.randint(1, 9)
    root = rng.randint(1, 12) * (rng.choice((1, -1)) if difficulty >= 3 else 1)
    c = rng.randint(1, a - 1)
    if difficulty == 1:
        return f"{a}(x + {b}) = {_join(_x_term(c), a * (root + b) - c * root)}"
    if difficulty == 2:
        return f"{a}(x - {b}) = {_join(_x_term(c), a * (root - b) - c * root)}"
    return f"{-a}(x - {b}) = {_join(_x_term(c), -a * (root - b) - c * root)}"


def _generate_transposition(rng: random.Random, difficulty: int) -> str:
    a = rng.randint(2, 7 + difficulty)
    c = rng.randint(1, a - 1)
    b = rng.randint(1, 15) * rng.choice((1, -1))
    root = rng.randint(1, 12) * (rng.choice((1, -1)) if difficulty >= 2 else 1)
    return f"{_join(_x_term(a), b)} = {_join(_x_term(c), a * root + b - c * root)}"


def _generate_isolation(rng: random.Random, difficulty: int) -> str:
    a = rng.randint(2, 6 + difficulty)
    b = rng.randint(1, 12) * rng.choice((1, -1))
    root = rng.randint(1, 12) * (rng.choice((1, -1)) if difficulty >= 2 else 1)
    return f"{_join(_x_term(a), b)} = {a * root + b}"


GENERATABLE_SKILLS = (*SKILLS, "combining_terms")

_GENERATORS = {
    "distribution": _generate_distribution,
    "transposition": _generate_transposition,
    "equality_balance": _generate_transposition,
    "combining_terms": _generate_transposition,
    "isolating_variable": _generate_isolation,
    "equation_reasoning": _generate_distribution,
}


def generate_problem(
    skill: str,
    difficulty: int = 1,
    previous: list[str] | None = None,
    rng: random.Random | None = None,
) -> str:
    """Author a verified problem without a language model; the safety net for the demo."""
    source = rng or random.Random()
    generator = _GENERATORS.get(skill, _generate_distribution)
    clamped = max(1, min(3, difficulty))
    for _ in range(50):
        candidate = generator(source, clamped)
        if problem_rejection_reason(candidate, skill, previous) is None:
            return candidate
    raise RuntimeError(f"Could not generate a problem for skill {skill!r}")


# ------------------------------------------------------------------------- expression mode
#
# "Expand and simplify" problems have no equals sign. A student's working is a chain of
# expressions ("= x^2 + 6x + 9 - x^2 + 2x = 8x + 9"), each of which must equal the original.


@dataclass(frozen=True)
class Expression:
    """Duck-types the parts of Equation that steps and views use."""

    node: Node

    @property
    def text(self) -> str:
        return to_text(self.node)

    @property
    def latex(self) -> str:
        return to_latex(self.node)

    @property
    def has_x(self) -> bool:
        return contains_x(self.node)

    @property
    def value(self) -> sp.Expr:
        return sp.expand(to_sympy(self.node))


def parse_problem_expression(text: str) -> Expression:
    if "=" in text:
        raise ParseError("An expression to simplify has no '=' sign")
    return Expression(parse_expression(text))


def is_simplified(expression: Expression) -> bool:
    """No brackets left, and every like term already combined."""
    if _has_brackets(expression.node):
        return False
    written = len(_signed_terms(expression.node))
    combined = len(sp.Add.make_args(expression.value)) if expression.value != 0 else 1
    return written == combined


def extract_expression_chain(text: str) -> list[Expression]:
    """Every expression a student writes, splitting chains on each '='."""
    found: list[Expression] = []
    for run in _math_runs(text):
        for part in run.split("="):
            part = part.strip()
            if not part:
                continue
            try:
                node = parse_expression(part)
            except ParseError:
                continue
            if contains_x(node):
                found.append(Expression(node))
    return found


def describe_expression_step(previous: Expression, new: Expression) -> str:
    def has_square(node: Node) -> bool:
        match node:
            case Pow(base, _):
                return isinstance(base, Add | Sub) or has_square(base)
            case Add(left, right) | Sub(left, right) | Mul(left, right) | Div(left, right):
                return has_square(left) or has_square(right)
            case Neg(operand):
                return has_square(operand)
        return False

    if has_square(previous.node) and not has_square(new.node):
        return "expanded_square"
    if _has_brackets(previous.node) and not _has_brackets(new.node):
        return "opened_brackets"
    return "combined_like_terms"


def diagnose_expression(previous: Expression, student: Expression) -> Diagnosis:
    target = student.value
    for variant in _wrong_expansions(previous.node):
        if sp.expand(variant.side_value) == target:
            if variant.kind.startswith("square"):
                return _square_diagnosis(variant)
            return Diagnosis(
                type=variant.kind,
                skill=SKILL_FOR_MISTAKE[variant.kind],
                evidence={
                    "bracket": f"{format_expr(variant.coefficient)}({variant.bracket})",
                    "correct_expansion": format_expr(variant.correct),
                    "student_expansion": format_expr(variant.wrong),
                },
            )
    delta = sp.expand(target - previous.value)
    if len(sp.Add.make_args(delta)) == 1:
        return Diagnosis(
            type="arithmetic_slip",
            skill=SKILL_FOR_MISTAKE["arithmetic_slip"],
            evidence={"difference_from_correct": format_expr(delta)},
        )
    return Diagnosis(type="unknown", skill=SKILL_FOR_MISTAKE["unknown"])


def _site_nodes(node: Node) -> Iterator[Node]:
    """Brackets and squares inside an expression: the parts a student may work on separately."""
    match node:
        case Mul(left, right):
            if isinstance(left, Add | Sub) or isinstance(right, Add | Sub):
                yield node
            yield from _site_nodes(left)
            yield from _site_nodes(right)
        case Pow(base, _):
            if isinstance(base, Add | Sub):
                yield node
            yield from _site_nodes(base)
        case Add(left, right) | Sub(left, right):
            yield from _site_nodes(left)
            yield from _site_nodes(right)
        case Neg(operand):
            yield from _site_nodes(operand)


def _check_part(original: Expression, current: Expression, item: Expression) -> StepCheck | None:
    """A student expanding one bracket on its own, e.g. "(x + 5)^2 = x^2 + 10x + 25"."""
    for site in [*_site_nodes(original.node), *_site_nodes(current.node)]:
        value = sp.expand(to_sympy(site))
        if item.value in (value, -value):
            return StepCheck(item, "valid", kind="worked_on_part")  # type: ignore[arg-type]
        for variant in _wrong_expansions(site):
            wrong = sp.expand(variant.side_value)
            if item.value in (wrong, -wrong):
                diagnosis = (
                    _square_diagnosis(variant)
                    if variant.kind.startswith("square")
                    else Diagnosis(
                        type=variant.kind,
                        skill=SKILL_FOR_MISTAKE[variant.kind],
                        evidence={
                            "bracket": to_text(site),
                            "correct_expansion": format_expr(variant.correct),
                            "student_expansion": format_expr(variant.wrong),
                        },
                    )
                )
                return StepCheck(item, "invalid", diagnosis)  # type: ignore[arg-type]
    return None


def check_expression_steps(
    original: Expression,
    last_valid: Expression,
    wrong: Expression | None,
    written: list[Expression],
) -> list[StepCheck]:
    checks: list[StepCheck] = []
    current = last_valid
    for expression in written:
        if expression.value != original.value:
            part = _check_part(original, current, expression)
            if part is not None:
                checks.append(part)
                continue
        if expression.value == original.value:
            if expression.text in (current.text, original.text):
                status = "restated"
            elif is_simplified(expression):
                status = "final_correct"
            else:
                status = "valid"
            kind = None if status == "restated" else describe_expression_step(current, expression)
            checks.append(StepCheck(expression, status, kind=kind))  # type: ignore[arg-type]
            if status == "final_correct":
                # The problem is done; whatever follows (say, turning it into an equation) is
                # not simplification and must not be graded as such.
                break
            current = expression
            wrong = None
            continue
        if wrong is not None and expression.value == wrong.value:
            checks.append(StepCheck(expression, "continues_error"))  # type: ignore[arg-type]
            continue
        diagnosis = diagnose_expression(current, expression)
        if diagnosis.type == "unknown" and current.text != original.text:
            fallback = diagnose_expression(original, expression)
            diagnosis = fallback if fallback.type != "unknown" else diagnosis
        checks.append(StepCheck(expression, "invalid", diagnosis))  # type: ignore[arg-type]
        wrong = expression
    return checks


def find_expression_reveal(
    reply: str,
    original: Expression,
    last_valid: Expression,
    student_expressions: list[Expression],
) -> str | None:
    """The simplified result, or the expansion of a bracket or square, given away in a reply."""
    known = {item.value for item in student_expressions}
    as_equation = Equation(original.node, Num(Fraction(0)))
    current = Equation(last_valid.node, Num(Fraction(0)))
    secrets = [original.value, *_distribution_results(as_equation), *_distribution_results(current)]
    secrets = [value for value in secrets if value not in known]
    for node in extract_expressions(reply):
        if _has_brackets(node):
            continue
        if sp.expand(to_sympy(node)) in secrets:
            return to_text(node)
    return None
