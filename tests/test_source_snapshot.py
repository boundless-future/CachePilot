import hashlib

from scripts.verify_source_snapshot import verify


def test_saved_source_requires_exact_line_endings_and_reports_additions(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = scripts / "sample.py"
    source.write_bytes(b"value = 1\n")
    plan = {"signature": {"source_sha256": {"scripts/sample.py": hashlib.sha256(source.read_bytes()).hexdigest()}}}
    (scripts / "new.py").write_text("addition")
    result = verify(plan, tmp_path)
    assert result["passed"] and result["additional_files"] == ["scripts/new.py"]
    source.write_bytes(b"value = 1\r\n")
    assert not verify(plan, tmp_path)["passed"]
    source.unlink()
    assert verify(plan, tmp_path)["failures"][0]["reason"] == "missing source"


def test_empty_or_escaping_manifest_is_rejected(tmp_path):
    assert not verify({"signature": {"source_sha256": {}}}, tmp_path)["passed"]
    result = verify({"signature": {"source_sha256": {"../outside.py": "fake"}}}, tmp_path)
    assert not result["passed"] and result["failures"][0]["reason"] == "outside source root"
