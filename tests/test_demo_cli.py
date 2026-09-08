"""The demo CLI: run one scenario on both arms and print the comparison."""

import json
import pathlib

from enrolment.demo.__main__ import main

REPO = pathlib.Path(__file__).resolve().parents[1]
SC06 = REPO / "attacks" / "scenarios" / "output-injection" / "SC-06.yaml"


def test_it_prints_both_arms_and_the_enrolment_each_left_behind(tmp_path, capsys):
    exit_code = main([
        "--scenario", str(SC06),
        "--run-dir", str(tmp_path / "demo"),
    ])

    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert "32541" in printed["on"]["enrolled_after"]
    assert "32541" not in printed["off"]["enrolled_after"]
