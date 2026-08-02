from pathlib import Path


def test_skill_package_has_required_layout():
    root = Path(__file__).parents[1]
    required = {
        "SKILL.md",
        "agents/openai.yaml",
        "scripts/select_paths.py",
        "scripts/engine_launcher.py",
        "rules/schema.json",
        "src/cashflow_main/__init__.py",
        "pyproject.toml",
    }
    actual = {
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file()
    }
    assert required <= actual


def test_package_declares_stable_version():
    root = Path(__file__).parents[1]
    namespace: dict[str, object] = {}
    source = (root / "src/cashflow_main/__init__.py").read_text(
        encoding="utf-8-sig"
    )
    exec(source, namespace)
    assert namespace["__version__"] == "0.1.0"
