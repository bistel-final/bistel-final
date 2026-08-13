"""prefetch_embedding_model.py 의 네트워크 없는 순수 검증을 다룬다."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "prefetch_embedding_model.py"
)
_spec = importlib.util.spec_from_file_location("prefetch_embedding_model", MODULE_PATH)
prefetch_embedding_model = importlib.util.module_from_spec(_spec)
sys.modules["prefetch_embedding_model"] = prefetch_embedding_model
_spec.loader.exec_module(prefetch_embedding_model)
pem = prefetch_embedding_model


def _write_model_file(
    cache_dir: Path, name: str = "config.json", text: str = "{}"
) -> Path:
    path = cache_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _valid_manifest(cache_dir: Path, revision: str = "fixed-revision") -> dict:
    _write_model_file(cache_dir)
    return pem.build_manifest(
        model_id="BAAI/bge-m3",
        revision=revision,
        cache_dir=cache_dir,
        embedding_dimension=1024,
    )


class TestResolveRevision:
    def test_env_revision_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL_REVISION", pem.EXPECTED_MODEL_REVISION)

        assert pem.resolve_revision() == pem.EXPECTED_MODEL_REVISION

    @pytest.mark.parametrize("value", [None, "", "   ", "change_me", "main"])
    def test_missing_empty_or_placeholder_revision_fails(
        self, monkeypatch: pytest.MonkeyPatch, value: str | None
    ) -> None:
        monkeypatch.delenv("EMBEDDING_MODEL_REVISION", raising=False)
        if value is not None:
            monkeypatch.setenv("EMBEDDING_MODEL_REVISION", value)

        with pytest.raises(RuntimeError, match="EMBEDDING_MODEL_REVISION"):
            pem.resolve_revision()

    def test_wrong_commit_hash_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "EMBEDDING_MODEL_REVISION",
            "0000000000000000000000000000000000000000",
        )

        with pytest.raises(RuntimeError, match="공식 revision"):
            pem.resolve_revision()


class TestResolveModelId:
    def test_env_model_id_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-m3")

        assert pem.resolve_model_id() == "BAAI/bge-m3"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_or_empty_model_id_fails(
        self, monkeypatch: pytest.MonkeyPatch, value: str | None
    ) -> None:
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
        if value is not None:
            monkeypatch.setenv("EMBEDDING_MODEL", value)

        with pytest.raises(RuntimeError, match="EMBEDDING_MODEL"):
            pem.resolve_model_id()

    def test_wrong_model_id_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/other-model")

        with pytest.raises(RuntimeError, match="BAAI/bge-m3"):
            pem.resolve_model_id()


class TestResolveEmbeddingDimension:
    def test_missing_dimension_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EMBEDDING_DIM", raising=False)

        with pytest.raises(RuntimeError, match="EMBEDDING_DIM"):
            pem.resolve_embedding_dimension()

    def test_env_dimension_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_DIM", "1024")

        assert pem.resolve_embedding_dimension() == 1024

    @pytest.mark.parametrize("value", ["abc", "1.5"])
    def test_non_integer_dimension_fails(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("EMBEDDING_DIM", value)

        with pytest.raises(RuntimeError, match="EMBEDDING_DIM 은 정수"):
            pem.resolve_embedding_dimension()

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_non_positive_dimension_fails(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("EMBEDDING_DIM", value)

        with pytest.raises(RuntimeError, match="EMBEDDING_DIM 은 1 이상"):
            pem.resolve_embedding_dimension()

    def test_wrong_dimension_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_DIM", "768")

        with pytest.raises(RuntimeError, match="1024"):
            pem.resolve_embedding_dimension()


class TestResolveCacheDir:
    def test_missing_cache_dir_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EMBEDDING_MODEL_PATH", raising=False)

        with pytest.raises(RuntimeError, match="EMBEDDING_MODEL_PATH"):
            pem.resolve_cache_dir()

    def test_relative_cache_dir_is_repo_relative(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL_PATH", "backend/model-cache/custom")

        assert pem.resolve_cache_dir() == (
            pem.REPOSITORY_ROOT / "backend" / "model-cache" / "custom"
        )

    def test_absolute_cache_dir_is_preserved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL_PATH", str(tmp_path))

        assert pem.resolve_cache_dir() == tmp_path


class TestCollectModelFiles:
    def test_collects_sha256_and_size(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        _write_model_file(cache_dir, text="abc")

        files = pem.collect_model_files(cache_dir)

        assert files == [
            {
                "path": "config.json",
                "size_bytes": 3,
                "sha256": (
                    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9c"
                    "b410ff61f20015ad"
                ),
            }
        ]

    def test_ignores_huggingface_cache_and_temp_files(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        _write_model_file(cache_dir, "config.json")
        _write_model_file(cache_dir, ".cache/metadata", "ignored")
        _write_model_file(cache_dir, "download.lock", "ignored")
        _write_model_file(cache_dir, "partial.tmp", "ignored")

        files = pem.collect_model_files(cache_dir)

        assert [f["path"] for f in files] == ["config.json"]

    def test_missing_cache_dir_fails(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="모델 캐시 디렉터리"):
            pem.collect_model_files(tmp_path / "missing")


class TestManifest:
    def test_build_manifest_records_model_metadata(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"

        manifest = _valid_manifest(cache_dir, revision="fixed-revision")

        assert manifest["format_version"] == pem.MANIFEST_FORMAT_VERSION
        assert manifest["hash_algorithm"] == pem.HASH_ALGORITHM
        assert manifest["model"]["id"] == "BAAI/bge-m3"
        assert manifest["model"]["revision"] == "fixed-revision"
        assert manifest["model"]["embedding_dimension"] == 1024
        assert manifest["files"][0]["path"] == "config.json"

    def test_save_and_load_manifest_round_trip(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest = _valid_manifest(cache_dir)
        manifest_path = tmp_path / "manifest.json"

        pem.save_manifest(manifest, manifest_path)

        assert pem.load_manifest(manifest_path) == manifest
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert saved == manifest

    def test_validate_manifest_accepts_valid_manifest(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest = _valid_manifest(cache_dir, revision="fixed-revision")

        assert (
            pem.validate_manifest(
                manifest=manifest,
                model_id="BAAI/bge-m3",
                revision="fixed-revision",
                cache_dir=cache_dir,
                embedding_dimension=1024,
            )
            == []
        )

    def test_revision_mismatch_is_reported(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest = _valid_manifest(cache_dir, revision="old-revision")

        errors = pem.validate_manifest(
            manifest=manifest,
            model_id="BAAI/bge-m3",
            revision="new-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
        )

        assert "model.revision 불일치" in errors

    def test_dimension_mismatch_is_reported(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest = _valid_manifest(cache_dir)

        errors = pem.validate_manifest(
            manifest=manifest,
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=768,
        )

        assert "model.embedding_dimension 불일치" in errors

    def test_missing_file_is_reported(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest = _valid_manifest(cache_dir)
        (cache_dir / "config.json").unlink()

        errors = pem.validate_manifest(
            manifest=manifest,
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
        )

        assert any("파일 누락" in error for error in errors)

    def test_hash_mismatch_is_reported(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest = _valid_manifest(cache_dir)
        _write_model_file(cache_dir, text="{changed}")

        errors = pem.validate_manifest(
            manifest=manifest,
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
        )

        assert any("sha256 불일치" in error for error in errors)

    def test_extra_cache_file_is_reported(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest = _valid_manifest(cache_dir)
        _write_model_file(cache_dir, "extra.bin", "unexpected")

        errors = pem.validate_manifest(
            manifest=manifest,
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
        )

        assert any("manifest 미등록 파일" in error for error in errors)

    def test_parent_path_reference_is_rejected(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest = _valid_manifest(cache_dir)
        manifest["files"][0]["path"] = "../outside"

        errors = pem.validate_manifest(
            manifest=manifest,
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
        )

        assert any("상대 하위 경로만 허용" in error for error in errors)

    def test_absolute_path_reference_is_rejected(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest = _valid_manifest(cache_dir)
        manifest["files"][0]["path"] = "C:/outside/config.json"

        errors = pem.validate_manifest(
            manifest=manifest,
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
        )

        assert any("상대 하위 경로만 허용" in error for error in errors)

    def test_duplicate_manifest_path_is_rejected(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest = _valid_manifest(cache_dir)
        manifest["files"].append(dict(manifest["files"][0]))

        errors = pem.validate_manifest(
            manifest=manifest,
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
        )

        assert any("manifest path 중복" in error for error in errors)


class TestRun:
    def test_dry_run_does_not_prefetch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        called = False

        def fake_prefetch(*_args: object, **_kwargs: object) -> Path:
            nonlocal called
            called = True
            return tmp_path

        monkeypatch.setattr(pem, "prefetch_model", fake_prefetch)

        result = pem.run(
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=tmp_path / "cache",
            embedding_dimension=1024,
            dry_run=True,
            verify_only=False,
            generate_manifest=False,
            manifest_path=tmp_path / "manifest.json",
        )

        assert result == 0
        assert not called

    def test_default_prefetch_verifies_existing_manifest_without_overwrite(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cache_dir = tmp_path / "cache"
        manifest_path = tmp_path / "manifest.json"
        _write_model_file(cache_dir)
        manifest = pem.build_manifest(
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
        )
        pem.save_manifest(manifest, manifest_path)
        before = manifest_path.read_text(encoding="utf-8")

        def fake_prefetch(model_id: str, revision: str, target_cache_dir: Path) -> Path:
            assert (model_id, revision) == ("BAAI/bge-m3", "fixed-revision")
            assert target_cache_dir == cache_dir
            return target_cache_dir

        monkeypatch.setattr(pem, "prefetch_model", fake_prefetch)

        result = pem.run(
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
            dry_run=False,
            verify_only=False,
            generate_manifest=False,
            manifest_path=manifest_path,
        )

        assert result == 0
        assert manifest_path.read_text(encoding="utf-8") == before

    def test_generate_manifest_writes_and_verifies_manifest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cache_dir = tmp_path / "cache"
        manifest_path = tmp_path / "manifest.json"

        def fake_prefetch(model_id: str, revision: str, target_cache_dir: Path) -> Path:
            assert (model_id, revision) == ("BAAI/bge-m3", "fixed-revision")
            _write_model_file(target_cache_dir)
            return target_cache_dir

        monkeypatch.setattr(pem, "prefetch_model", fake_prefetch)

        result = pem.run(
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
            dry_run=False,
            verify_only=False,
            generate_manifest=True,
            manifest_path=manifest_path,
        )

        assert result == 0
        assert pem.load_manifest(manifest_path)["model"]["revision"] == "fixed-revision"

    def test_verify_only_uses_existing_manifest(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        manifest_path = tmp_path / "manifest.json"
        pem.save_manifest(_valid_manifest(cache_dir), manifest_path)

        result = pem.run(
            model_id="BAAI/bge-m3",
            revision="fixed-revision",
            cache_dir=cache_dir,
            embedding_dimension=1024,
            dry_run=False,
            verify_only=True,
            generate_manifest=False,
            manifest_path=manifest_path,
        )

        assert result == 0
