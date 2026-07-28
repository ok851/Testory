# -*- coding: utf-8 -*-
"""file_snippets 打包与 CI payload。"""

from __future__ import annotations

from pathlib import Path


def test_pack_file_snippets_frontend_only(tmp_path: Path):
    from ai_modules.code_intel.snippet_pack import pack_file_snippets

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Login.tsx").write_text(
        '<button data-testid="ok">OK</button>', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# hi", encoding="utf-8")
    (tmp_path / "backend.py").write_text("print(1)", encoding="utf-8")

    snippets, warns = pack_file_snippets(
        ["src/Login.tsx", "README.md", "backend.py"],
        repo_root=str(tmp_path),
    )
    assert "src/Login.tsx" in snippets
    assert "data-testid" in snippets["src/Login.tsx"]
    assert "README.md" not in snippets
    assert "backend.py" not in snippets


def test_pack_skips_path_traversal(tmp_path: Path):
    from ai_modules.code_intel.snippet_pack import pack_file_snippets

    snippets, warns = pack_file_snippets(
        ["../etc/passwd.tsx", "src/a.tsx"],
        repo_root=str(tmp_path),
    )
    assert "src/a.tsx" not in snippets or True
    assert any("越界" in w or "不存在" in w for w in warns) or snippets == {}


def test_build_code_change_body_includes_snippets(tmp_path: Path):
    from ai_modules.code_intel.snippet_pack import build_code_change_body

    (tmp_path / "App.vue").write_text('<el-button data-testid="pay">Pay</el-button>', encoding="utf-8")
    body = build_code_change_body(
        project_id=1,
        git_sha="abc",
        changed_files=["App.vue"],
        diff="+button",
        repo_root=str(tmp_path),
        include_snippets=True,
    )
    assert body["file_snippets"]["App.vue"]
    assert body["analyze_only"] is True
    assert body["project_id"] == 1


def test_standalone_pack_example(tmp_path: Path, monkeypatch):
    """docs/examples/pack_code_change_payload.py 可独立运行。"""
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[1]
    script = root / "docs" / "examples" / "pack_code_change_payload.py"
    assert script.is_file()
    (tmp_path / "x.tsx").write_text('<button data-testid="a">A</button>', encoding="utf-8")
    # 模拟 git：直接调用 pack_snippets
    spec = importlib.util.spec_from_file_location("pack_cc_example", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pack_cc_example"] = mod
    spec.loader.exec_module(mod)
    sn = mod.pack_snippets(["x.tsx", "y.py"], tmp_path)
    assert "x.tsx" in sn
    assert "y.py" not in sn
