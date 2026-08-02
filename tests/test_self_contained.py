import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_runtime_has_no_reference_to_old_skill_or_desktop_sources(tmp_path):
    forbidden = ("cas-cashflow-indirect", "间接法编制现流表")
    paths = [ROOT / "SKILL.md", *ROOT.joinpath("scripts").glob("*.py"), *ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("rules").glob("*.json")]
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        assert not any(value in text for value in forbidden)


def test_launcher_works_from_an_unrelated_current_directory(tmp_path):
    completed = subprocess.run([sys.executable, str(ROOT / "scripts/engine_launcher.py"), "--help"], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0
    assert "prepare" in completed.stdout and "finalize" in completed.stdout and "status" in completed.stdout
