import json

import pytest

from enrolment.models import CompletedSubject, Student
from enrolment.state import (
    AlreadyEnrolledError,
    NotEnrolledError,
    RunState,
    UnknownStudentError,
)


def write_students(tmp_path, **overrides):
    student = Student(
        student_id="S1000001",
        course="C04296",
        admitted_to=["C04296"],
        completed=[CompletedSubject(code="32541", session="2025-AUT", credit_points=6)],
        enrolled=["43030"],
        **overrides,
    )
    path = tmp_path / "students.json"
    path.write_text(json.dumps({student.student_id: student.model_dump()}))
    return path


def test_load_reads_every_student(tmp_path):
    state = RunState.load(write_students(tmp_path))
    assert state.get("S1000001").course == "C04296"


def test_unknown_student_raises(tmp_path):
    state = RunState.load(write_students(tmp_path))
    with pytest.raises(UnknownStudentError):
        state.get("S9999999")


def test_enrol_adds_the_subject(tmp_path):
    state = RunState.load(write_students(tmp_path))
    state.enrol("S1000001", "32144")
    assert "32144" in state.get("S1000001").enrolled


def test_enrolling_twice_raises(tmp_path):
    state = RunState.load(write_students(tmp_path))
    with pytest.raises(AlreadyEnrolledError):
        state.enrol("S1000001", "43030")


def test_drop_removes_the_subject(tmp_path):
    state = RunState.load(write_students(tmp_path))
    state.drop("S1000001", "43030")
    assert "43030" not in state.get("S1000001").enrolled


def test_dropping_what_is_not_held_raises(tmp_path):
    state = RunState.load(write_students(tmp_path))
    with pytest.raises(NotEnrolledError):
        state.drop("S1000001", "32144")


def test_mutations_persist_to_the_run_directory(tmp_path):
    run_dir = tmp_path / "run"
    state = RunState.load(write_students(tmp_path), run_dir=run_dir)
    state.enrol("S1000001", "32144")
    written = json.loads((run_dir / "state.json").read_text())
    assert "32144" in written["S1000001"]["enrolled"]


def test_the_source_file_is_never_modified(tmp_path):
    """The committed dataset is read-only; a run must not write back to it."""
    source = write_students(tmp_path)
    before = source.read_text()
    state = RunState.load(source, run_dir=tmp_path / "run")
    state.enrol("S1000001", "32144")
    state.drop("S1000001", "43030")
    assert source.read_text() == before


def test_reads_do_not_mutate(tmp_path):
    state = RunState.load(write_students(tmp_path), run_dir=tmp_path / "run")
    first = state.get("S1000001").model_dump()
    state.get("S1000001").enrolled.append("99999")
    assert state.get("S1000001").model_dump() == first


def test_persistent_property_reflects_run_dir(tmp_path):
    ephemeral = RunState.load(write_students(tmp_path))
    assert ephemeral.persistent is False

    durable = RunState.load(write_students(tmp_path), run_dir=tmp_path / "run")
    assert durable.persistent is True


def test_ephemeral_state_does_not_persist(tmp_path):
    """Mutating an ephemeral state changes memory but writes nothing to disk.

    Regression guard for the finding: silent no-op when run_dir is None.
    """
    state = RunState.load(write_students(tmp_path))
    assert state.persistent is False
    state.enrol("S1000001", "32144")
    assert "32144" in state.get("S1000001").enrolled
    assert not (tmp_path / "state.json").exists()
