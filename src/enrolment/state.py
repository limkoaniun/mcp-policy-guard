"""Run-scoped mutable student state.

The committed dataset under `data/` is the fixture every run starts from and
is never written to. A run loads it, mutates its own copy, and persists that
copy under `runs/<run-id>/state.json` after every mutation — which is what the
evaluation harness reads afterwards to decide whether an attack succeeded.

Scoring on final state rather than on model output is what keeps the metric
free of judge noise, so this file is the ground truth for every experiment.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Student


class UnknownStudentError(KeyError):
    """No such student in this run's state."""


class NotEnrolledError(ValueError):
    """The student does not hold the subject being dropped."""


class AlreadyEnrolledError(ValueError):
    """The student already holds the subject being enrolled in."""


class RunDirectoryInUseError(FileExistsError):
    """The run directory already holds a `state.json`.

    A run directory is single-use. Loading into one that already has state
    would overwrite it with the baseline, while `DecisionLog` *appends* across
    restarts -- so the pair of artifacts the harness scores would show a
    forwarded mutation in the log and an unmutated state file, which reads as
    "enforcement worked" when in fact the evidence was destroyed.
    """


class RunState:
    def __init__(self, students: dict[str, Student], path: Path | None = None):
        self._students = students
        self.path = path

    @classmethod
    def load(cls, students_path, run_dir=None) -> RunState:
        raw = json.loads(Path(students_path).read_text(encoding="utf-8"))
        students = {code: Student.model_validate(value) for code, value in raw.items()}
        path = None
        if run_dir is not None:
            path = Path(run_dir) / "state.json"
            if path.exists():
                # Spec 5: a fresh server process per scenario run, and so a
                # fresh run directory. Resetting an existing state.json to the
                # baseline here would silently discard the mutations an earlier
                # load recorded.
                raise RunDirectoryInUseError(
                    f"{path} already exists: a run directory is single-use. "
                    "Loading into it would reset state.json to the baseline "
                    "while decisions.jsonl kept appending, leaving a log that "
                    "shows a forwarded mutation beside a state file that shows "
                    "none -- which scores as a successful defence."
                )
            path.parent.mkdir(parents=True, exist_ok=True)
        state = cls(students, path)
        if state.persistent:
            # Write the baseline now so the file's *existence* means "this run got
            # far enough to mutate state" and only its *content* says whether it
            # did. Without this, a blocked attack and a crashed run are
            # indistinguishable to the evaluation harness, and a crash reads as a
            # successful defence.
            state.save()
        return state

    def get(self, student_id: str) -> Student:
        """Return a copy: callers must not be able to mutate state by reading it."""
        try:
            return self._students[student_id].model_copy(deep=True)
        except KeyError:
            raise UnknownStudentError(f"no such student: {student_id}") from None

    def _require(self, student_id: str) -> Student:
        try:
            return self._students[student_id]
        except KeyError:
            raise UnknownStudentError(f"no such student: {student_id}") from None

    @property
    def persistent(self) -> bool:
        """Whether mutations are being recorded.

        False means this state is ephemeral: `enrol` and `drop` change memory
        and nothing else. That is legitimate for unit tests, and wrong for a
        run whose final state is the experiment's ground truth — a caller that
        needs a durable record should assert on this rather than assume it.
        """
        return self.path is not None

    def enrol(self, student_id: str, code: str) -> None:
        student = self._require(student_id)
        if code in student.enrolled:
            raise AlreadyEnrolledError(f"{student_id} is already enrolled in {code}")
        student.enrolled.append(code)
        self.save()

    def drop(self, student_id: str, code: str) -> None:
        student = self._require(student_id)
        if code not in student.enrolled:
            raise NotEnrolledError(f"{student_id} is not enrolled in {code}")
        student.enrolled.remove(code)
        self.save()

    def save(self) -> None:
        if self.path is None:
            # Ephemeral by construction — see `persistent`. Deliberately not an
            # error: the unit tests use this mode. Callers whose state is
            # ground truth must check `persistent` themselves.
            return
        payload = {code: student.model_dump() for code, student in self._students.items()}
        self.path.write_text(
            json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
        )
