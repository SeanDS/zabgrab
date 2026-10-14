"""Fake implementations of the service ports."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from zabgrab.errors import InputRequired, ZabgrabError
from zabgrab.services.ports import Option
from zabgrab.ui.console import non_interactive_answer

T = TypeVar("T")


@dataclass
class RecordingProgress:
    """Records progress events."""

    description: str
    total: int
    labels: list[str] = field(default_factory=list)
    steps: list[int] = field(default_factory=list)
    active: set[int] = field(default_factory=set)
    max_active: int = 0
    finished: bool = False

    def add_item(self, label: str, total: int) -> int:
        self.labels.append(label)
        item = len(self.labels) - 1
        self.steps.append(0)
        self.active.add(item)
        self.max_active = max(self.max_active, len(self.active))
        return item

    def advance(self, item: int) -> None:
        assert item in self.active
        self.steps[item] += 1

    def remove_item(self, item: int) -> None:
        self.active.remove(item)

    @property
    def done(self) -> int:
        return sum(self.steps)


@dataclass
class RecordingUI:
    """A UI that records messages and answers prompts from a script.

    ``answers`` are consumed in order: a ``bool`` for :meth:`confirm`, a ``str`` for
    :meth:`prompt` (passed through ``parse``), an option index for :meth:`choose`
    and a list of indices for :meth:`choose_many`. When ``interactive`` is false,
    prompts raise :class:`InputRequired` like the real UI.
    """

    answers: list[Any] = field(default_factory=list)
    interactive: bool = True
    messages: list[tuple[str, str]] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    secret_questions: list[str] = field(default_factory=list)
    progresses: list[RecordingProgress] = field(default_factory=list)

    def _record(self, level: str, message: str) -> None:
        self.messages.append((level, message))

    def debug(self, message: str) -> None:
        self._record("debug", message)

    def info(self, message: str) -> None:
        self._record("info", message)

    def success(self, message: str) -> None:
        self._record("success", message)

    def warning(self, message: str) -> None:
        self._record("warning", message)

    def error(
        self, message: str, *, details: Sequence[str] = (), hint: str | None = None
    ) -> None:
        self._record("error", message)

    def _answer(self, question: str, fix: str) -> Any:
        if not self.interactive:
            raise InputRequired(question, fix=fix)
        self.questions.append(question)
        if not self.answers:
            raise AssertionError(f"Unexpected prompt: {question!r}")
        return self.answers.pop(0)

    def confirm(self, question: str, *, fix: str, default: bool = True) -> bool:
        if not self.interactive:
            self.warning(non_interactive_answer(question, default, fix))
            return default
        answer = self._answer(question, fix)
        assert isinstance(answer, bool)
        return answer

    def prompt(
        self,
        question: str,
        *,
        fix: str,
        parse: Callable[[str], T],
        default: str | None = None,
        secret: bool = False,
    ) -> T:
        if secret:
            self.secret_questions.append(question)
        return parse(self._answer(question, fix))

    def choose(
        self,
        question: str,
        options: Sequence[Option[T]],
        *,
        fix: str,
        default: int | None = None,
    ) -> T:
        index: int = self._answer(question, fix)
        return options[index].value

    def choose_many(
        self, question: str, options: Sequence[Option[T]], *, fix: str
    ) -> list[T]:
        return [options[i].value for i in self._answer(question, fix)]

    @contextmanager
    def progress(self, description: str, total: int) -> Iterator[RecordingProgress]:
        progress = RecordingProgress(description, total)
        self.progresses.append(progress)
        yield progress
        progress.finished = True

    def levels(self, level: str) -> list[str]:
        """Messages recorded at ``level``."""
        return [message for lvl, message in self.messages if lvl == level]


@dataclass
class FakeEditor:
    """An editor that applies scripted contents to the file each time it's opened."""

    contents: list[str] = field(default_factory=list)
    opened: list[Path] = field(default_factory=list)

    def edit(self, path: Path) -> None:
        self.opened.append(path)
        if self.contents:
            path.write_text(self.contents.pop(0), encoding="utf-8")


@dataclass
class FrozenClock:
    """A clock that always returns the same time."""

    time: datetime

    def now(self) -> datetime:
        return self.time


@dataclass
class MemorySecrets:
    """A token store in memory. ``fail`` makes saving fail like a broken keyring."""

    tokens: dict[str, str] = field(default_factory=dict)
    fail: bool = False

    def get_token(self, url: str) -> str | None:
        return self.tokens.get(url)

    def set_token(self, url: str, token: str) -> None:
        if self.fail:
            raise ZabgrabError("Couldn't save the token.", hint="Use token_env.")
        self.tokens[url] = token

    def delete_token(self, url: str) -> bool:
        return self.tokens.pop(url, None) is not None

    def urls(self) -> list[str]:
        return sorted(self.tokens)

    def delete_all(self) -> int:
        count = len(self.tokens)
        self.tokens.clear()
        return count
