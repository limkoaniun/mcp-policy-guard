import pytest

from policy_guard.config import build_context, load_policy_pack, load_task

TASK = """
task_id: OI-001
description: Tell me about 32144
allowed_tools: [search_subjects, get_subject_details]
student_id: S1000001
overlay: attacks/scenarios/output-injection/OI-001.overlay.yaml
"""

PACK = """
name: enrolment
upstream_command: python
upstream_args: [-m, enrolment.server]
policies: [allowlist]
trust:
  search_subjects: untrusted
  get_subject_details: untrusted
  get_student_record: trusted
"""


def test_task_loads(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text(TASK)
    task = load_task(path)
    assert task.task_id == "OI-001"
    assert task.allowed_tools == ["search_subjects", "get_subject_details"]
    assert task.student_id == "S1000001"


def test_context_carries_the_allowlist_as_a_frozenset(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text(TASK)
    context = build_context(load_task(path))
    assert context.allowed_tools == frozenset({"search_subjects", "get_subject_details"})
    assert context.task_id == "OI-001"


def test_a_task_with_no_allowed_tools_denies_everything(tmp_path):
    """Deny-by-default has to survive a config that simply omits the list."""
    path = tmp_path / "task.yaml"
    path.write_text("task_id: T\ndescription: nothing declared\n")
    context = build_context(load_task(path))
    assert context.allowed_tools == frozenset()


def test_policy_pack_loads(tmp_path):
    path = tmp_path / "pack.yaml"
    path.write_text(PACK)
    pack = load_policy_pack(path)
    assert pack.upstream_command == "python"
    assert pack.upstream_args == ["-m", "enrolment.server"]
    assert pack.trust["get_subject_details"] == "untrusted"
    assert pack.policies == ["allowlist"]


def test_an_unknown_trust_label_is_rejected(tmp_path):
    path = tmp_path / "pack.yaml"
    path.write_text(PACK.replace("get_student_record: trusted", "get_student_record: maybe"))
    with pytest.raises(ValueError, match="maybe"):
        load_policy_pack(path)


def test_the_shipped_enrolment_pack_is_valid():
    pack = load_policy_pack("configs/enrolment.yaml")
    assert pack.name == "enrolment"
    assert "get_subject_details" in pack.trust
