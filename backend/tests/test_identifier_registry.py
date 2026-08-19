import pytest

from app.metadata.identifier_registry import (
    DuplicateIdentifierError,
    IdentifierRegistryConfigurationError,
    SQLiteIdentifierRegistry,
)
from tests.fixtures import VALID_DOCUMENT


def test_committed_identifier_can_be_reused_for_same_content(tmp_path):
    registry = SQLiteIdentifierRegistry(str(tmp_path / "ids.sqlite3"))
    registry.reserve(VALID_DOCUMENT, "content-hash-1").commit()

    registry.reserve(VALID_DOCUMENT, "content-hash-1").commit()


def test_identifier_cannot_be_reused_for_different_content(tmp_path):
    database = tmp_path / "ids.sqlite3"
    SQLiteIdentifierRegistry(str(database)).reserve(
        VALID_DOCUMENT, "content-hash-1"
    ).commit()

    reloaded = SQLiteIdentifierRegistry(str(database))
    with pytest.raises(DuplicateIdentifierError):
        reloaded.reserve(VALID_DOCUMENT, "content-hash-2")


def test_rollback_releases_identifier(tmp_path):
    registry = SQLiteIdentifierRegistry(str(tmp_path / "ids.sqlite3"))
    registry.reserve(VALID_DOCUMENT, "content-hash-1").rollback()

    registry.reserve(VALID_DOCUMENT, "content-hash-2").commit()


def test_environment_path_is_required(monkeypatch):
    monkeypatch.delenv("AIGC_ID_REGISTRY_PATH", raising=False)
    with pytest.raises(IdentifierRegistryConfigurationError):
        SQLiteIdentifierRegistry.from_environment()


def test_registry_can_be_configured_from_environment(tmp_path, monkeypatch):
    database = tmp_path / "configured" / "ids.sqlite3"
    monkeypatch.setenv("AIGC_ID_REGISTRY_PATH", str(database))

    registry = SQLiteIdentifierRegistry.from_environment()
    registry.reserve(VALID_DOCUMENT, "content-hash-1").commit()

    assert database.is_file()
