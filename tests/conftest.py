from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def init_git_repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "arbiter@example.com"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Arbiter"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(root), check=True, capture_output=True, text=True)
    return root


@pytest.fixture()
def python_bug_repo(tmp_path: Path) -> Path:
    return init_git_repo(
        tmp_path / "python_bug_repo",
        {
            "calc.py": "def add(a, b):\n    return a - b\n",
            "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        },
    )


@pytest.fixture()
def ts_repo(tmp_path: Path) -> Path:
    return init_git_repo(
        tmp_path / "ts_repo",
        {
            "package.json": '{\n  "name": "demo",\n  "scripts": {\n    "test": "node test.js",\n    "lint": "node -e \\"process.exit(0)\\""\n  }\n}\n',
            "index.js": "function add(a, b) { return a - b; }\nmodule.exports = { add };\n",
            "test.js": "const { add } = require('./index'); if (add(2, 3) !== 5) { process.exit(1); }\n",
        },
    )

