"""Language adapter registry and incremental (content-hash cached) parsing."""
import shutil
from pathlib import Path

from app.engine.model import build_model, inventory
from app.engine.parse_cache import FileParseCache
from app.engine.pipeline import run_analysis
from app.parsers import registry
from app.parsers.base import LanguageAdapter
from app.parsers.detect import detect_language

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "banking-core"
COMPARED = ("model", "graph", "lineage", "rules", "docs", "chunks", "symbols")


def _copy_sample(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    shutil.copytree(SAMPLE, root)
    return root


def test_every_inventory_language_has_an_adapter():
    for f in inventory(SAMPLE):
        assert registry.get(f["language"]) is not None, f["language"]


def test_second_run_reuses_every_file_and_gives_identical_results(tmp_path):
    root = _copy_sample(tmp_path)
    first_cache = FileParseCache(tmp_path / "cache")
    first = run_analysis(root, "Core Banking", cache=first_cache)
    n = first["insights"]["overview"]["files"]
    assert first_cache.stats() == {"files_reused": 0, "files_parsed": n}

    second_cache = FileParseCache(tmp_path / "cache")
    second = run_analysis(root, "Core Banking", cache=second_cache)
    assert second_cache.stats() == {"files_reused": n, "files_parsed": 0}
    assert second["insights"]["overview"]["files_reused"] == n
    for kind in COMPARED:
        assert first[kind] == second[kind], kind

    uncached = run_analysis(root, "Core Banking")  # no cache at all: same answers
    for kind in COMPARED:
        assert uncached[kind] == second[kind], kind


def test_only_changed_files_are_parsed_and_stale_entries_pruned(tmp_path):
    root = _copy_sample(tmp_path)
    cache_dir = tmp_path / "cache"
    run_analysis(root, "Core Banking", cache=FileParseCache(cache_dir))
    entries_before = len(list(cache_dir.glob("*.json")))

    report = root / "cobol" / "REPORT12.CBL"
    report.write_text(report.read_text().replace("REPORT 12,", "REPORT 12 (REVISED),"))
    (root / "cobol" / "LEGACYTX.CBL").unlink()

    cache = FileParseCache(cache_dir)
    result = run_analysis(root, "Core Banking", cache=cache)
    assert cache.stats()["files_parsed"] == 1
    assert "LEGACYTX" not in result["model"]["programs"]
    # the old REPORT12 entry and the deleted LEGACYTX entry are gone; the new REPORT12 entry was added
    assert len(list(cache_dir.glob("*.json"))) == entries_before - 1


def test_corrupt_cache_entry_is_reparsed(tmp_path):
    root = _copy_sample(tmp_path)
    cache_dir = tmp_path / "cache"
    files = inventory(root)
    build_model(root, files, FileParseCache(cache_dir))
    victim = next(cache_dir.glob("cobol-*.json"))
    victim.write_text("{not json")
    cache = FileParseCache(cache_dir)
    model = build_model(root, files, cache)
    assert cache.stats()["files_parsed"] == 1 and len(model["programs"]) == 6


def test_new_language_plugs_in_through_one_adapter(tmp_path, monkeypatch):
    from app.parsers import detect

    monkeypatch.setattr(registry, "_ADAPTERS", dict(registry._ADAPTERS))
    # setitem records that the key was absent, so it is removed again after the test
    monkeypatch.setitem(detect.EXTENSION_LANGUAGE, ".nattest", "natural-test")

    class NaturalAdapter(LanguageAdapter):
        language = "natural-test"
        extensions = (".nattest",)

        def parse(self, path, text):
            return {"name": Path(path).stem.upper(), "lines": len(text.splitlines())}

        def merge(self, model, path, fragment):
            model["documents"].append({"path": path, "title": f"Natural module {fragment['name']}"})

    registry.register(NaturalAdapter())
    (tmp_path / "CUSTINQ.nattest").write_text("DEFINE DATA LOCAL\nEND-DEFINE\nEND\n")
    assert detect_language(tmp_path / "CUSTINQ.nattest", "") == "natural-test"
    files = inventory(tmp_path)
    model = build_model(tmp_path, files)
    assert model["documents"] == [{"path": "CUSTINQ.nattest", "title": "Natural module CUSTINQ"}]
