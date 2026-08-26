"""Parser and evaluator for UTS requisite rule expressions.

UTS publishes requisites as a boolean expression over labelled items, e.g.
`((1 AND (2 OR 2a OR 2b)) OR (3) OR (4))`, alongside a table mapping each
label to a subject, credit-point threshold, area of study or admission.

This module handles the expression only. It knows nothing about subjects or
students, which is what makes it testable against a plain truth table.

Grammar:
    expr   := term { "OR" term }
    term   := factor { "AND" factor }
    factor := "(" expr ")" | label
    label  := digit+ letter*
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TOKEN_RE = re.compile(r"\(|\)|AND\b|OR\b|\d+[a-z]*")


class RuleSyntaxError(ValueError):
    """Raised when a rule expression does not match the grammar."""


@dataclass(frozen=True)
class Label:
    name: str


@dataclass(frozen=True)
class And:
    parts: tuple[Node, ...]


@dataclass(frozen=True)
class Or:
    parts: tuple[Node, ...]


Node = Label | And | Or


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        match = TOKEN_RE.match(text, pos)
        if match is None:
            raise RuleSyntaxError(f"unexpected character {text[pos]!r} at position {pos}")
        tokens.append(match.group(0))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        token = self.peek()
        if token is None:
            raise RuleSyntaxError("unexpected end of rule")
        self.pos += 1
        return token

    def expr(self) -> Node:
        parts = [self.term()]
        while self.peek() == "OR":
            self.take()
            parts.append(self.term())
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def term(self) -> Node:
        parts = [self.factor()]
        while self.peek() == "AND":
            self.take()
            parts.append(self.factor())
        return parts[0] if len(parts) == 1 else And(tuple(parts))

    def factor(self) -> Node:
        token = self.take()
        if token == "(":
            node = self.expr()
            closing = self.take()
            if closing != ")":
                raise RuleSyntaxError(f"expected ')' but found {closing!r}")
            return node
        if token in ("AND", "OR", ")"):
            raise RuleSyntaxError(f"expected a label or '(' but found {token!r}")
        return Label(token)


def parse(text: str) -> Node:
    tokens = tokenize(text)
    if not tokens:
        raise RuleSyntaxError("empty rule")
    parser = _Parser(tokens)
    node = parser.expr()
    if parser.peek() is not None:
        raise RuleSyntaxError(f"trailing input at token {parser.peek()!r}")
    return node


def evaluate(node: Node, truth: dict[str, bool]) -> bool:
    if isinstance(node, Label):
        return truth.get(node.name, False)
    if isinstance(node, And):
        return all(evaluate(part, truth) for part in node.parts)
    return any(evaluate(part, truth) for part in node.parts)


def labels(node: Node) -> frozenset[str]:
    if isinstance(node, Label):
        return frozenset({node.name})
    return frozenset().union(*(labels(part) for part in node.parts))


def depth(node: Node) -> int:
    """Nesting depth, used to stratify generated scenarios by difficulty."""
    if isinstance(node, Label):
        return 1
    return 1 + max(depth(part) for part in node.parts)


def minimal_unmet(node: Node, truth: dict[str, bool]) -> frozenset[str]:
    """Smallest set of labels that would have to become true to satisfy `node`.

    Exact for the tree-shaped expressions UTS publishes: AND unions its
    children's requirements, OR takes the cheapest branch.
    """
    if isinstance(node, Label):
        return frozenset() if truth.get(node.name, False) else frozenset({node.name})
    if isinstance(node, And):
        return frozenset().union(*(minimal_unmet(part, truth) for part in node.parts))
    return min((minimal_unmet(part, truth) for part in node.parts), key=len)
