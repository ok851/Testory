# -*- coding: utf-8 -*-
from pathlib import Path

from modules.ai.ai_config_paths import (
    _iter_data_file_candidates,
    ai_provider_catalog_path,
    load_ai_provider_catalog_dict,
    resolve_install_root,
)


def test_ai_provider_catalog_exists_in_dev_tree() -> None:
    p = ai_provider_catalog_path()
    assert p.is_file(), f"missing catalog at {p}"
    raw = p.read_text(encoding="utf-8")
    assert '"providers"' in raw
    assert resolve_install_root().is_dir()


def test_load_ai_provider_catalog_has_providers() -> None:
    cat = load_ai_provider_catalog_dict()
    assert isinstance(cat.get("providers"), list)
    assert len(cat["providers"]) >= 10


def test_iter_candidates_includes_config_copy(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config" / "ai_provider_catalog.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"providers":[{"id":"x"}]}', encoding="utf-8")
    monkeypatch.setenv("TESTORY_INSTALL_ROOT", str(tmp_path))
    paths = _iter_data_file_candidates("ai_provider_catalog.json")
    assert any(p == cfg for p in paths)
