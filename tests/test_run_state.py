import json

import pytest

from enrolment.models import CompletedSubject, Student
from enrolment.state import (
    AlreadyEnrolledError,
    NotEnrolledError,
    RunDirectoryInUseError,
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


def test_a_persistent_load_writes_the_baseline_immediately(tmp_path):
    """`state.json`'s existence must mean "this run got far enough to mutate
    state", independent of whether any mutation happened.

    Without this, a blocked attack (no mutation) and a crashed run (also no
    mutation) are indistinguishable to the evaluation harness reading
    `runs/<id>/state.json` after the fact, and a crash reads as a successful
    defence -- inflating exactly the metric slices 2-4 exist to measure.
    """
    source = write_students(tmp_path)
    run_dir = tmp_path / "run"
    RunState.load(source, run_dir=run_dir)
    written = json.loads((run_dir / "state.json").read_text())
    assert written == json.loads(source.read_text())


def test_an_ephemeral_load_writes_nothing(tmp_path):
    """The baseline write is conditional on `persistent`, not unconditional."""
    RunState.load(write_students(tmp_path))
    assert not (tmp_path / "state.json").exists()


def test_a_run_directory_is_single_use(tmp_path):
    """A second load into the same run directory is refused, not silently reset.

    `RunState.load` writes the baseline over whatever is there, while
    `DecisionLog` *appends* across restarts. Reusing a run directory therefore
    produced a decision log showing a forwarded `drop` beside a state file
    showing the student still enrolled -- which the harness reads as
    "enforcement worked", when in fact the evidence of the mutation was
    overwritten. Spec 5 calls for a fresh server process per scenario run;
    this makes reusing one fail loudly instead.
    """
    source = write_students(tmp_path)
    run_dir = tmp_path / "run"
    state = RunState.load(source, run_dir=run_dir)
    state.enrol("S1000001", "32144")

    with pytest.raises(RunDirectoryInUseError, match="single-use"):
        RunState.load(source, run_dir=run_dir)

    # and the first run's evidence is still on disk, unreset
    written = json.loads((run_dir / "state.json").read_text())
    assert "32144" in written["S1000001"]["enrolled"]


def test_save_writes_the_current_state_to_its_path(tmp_path):
    """`save()` called directly does what it says.

    Every other persistence test goes through `enrol`/`drop`, which call
    `save()` themselves; this pins the method's own behaviour.
    """
    source = write_students(tmp_path)
    raw = json.loads(source.read_text())
    students = {code: Student.model_validate(value) for code, value in raw.items()}
    path = tmp_path / "run" / "state.json"
    path.parent.mkdir(parents=True)

    state = RunState(students, path)
    assert not path.exists()
    state.save()
    assert json.loads(path.read_text()) == raw
