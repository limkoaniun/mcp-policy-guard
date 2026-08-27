"""Tests for the CLI's upstream-command resolution.

`resolve_upstream_command` exists because a pack that names a bare `python`
interpreter must run the upstream under the same interpreter as the proxy
itself -- not whichever `python`/`python3` happens to be first on PATH, which
may not exist at all (see task-7-report.md, Finding 2).
"""

import sys

from policy_guard.__main__ import resolve_upstream_command


def test_a_bare_python_resolves_to_this_interpreter():
    assert resolve_upstream_command("python") == sys.executable


def test_a_bare_python3_resolves_to_this_interpreter():
    assert resolve_upstream_command("python3") == sys.executable


def test_a_specific_executable_is_passed_through_untouched():
    assert resolve_upstream_command("/usr/bin/node") == "/usr/bin/node"
