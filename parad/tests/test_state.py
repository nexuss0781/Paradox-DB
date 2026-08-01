"""Hermetic tests for parad.state (no network)."""

import pytest

import parad.config as config
from parad.state import (
    clear_dirty,
    get_remote_version,
    get_sync_status,
    is_dirty,
    is_offline,
    load_state,
    mark_dirty,
    sanitize_state_key,
    save_state,
    set_last_local_hash,
    set_offline,
    set_remote_version,
)


def test_sanitize_state_key():
    assert sanitize_state_key("a/b") == "a__b"
    assert sanitize_state_key("myproject/mydb") == "myproject__mydb"
    assert sanitize_state_key("myproject__mydb") == "myproject__mydb"
    assert sanitize_state_key("main") == "main"
    assert sanitize_state_key("a\\b") == "a__b"
    assert sanitize_state_key("a:b") == "a__b"
    assert sanitize_state_key("a b") == "a b"


def test_sanitize_state_key_rejects_empty():
    with pytest.raises(ValueError):
        sanitize_state_key("")
    with pytest.raises(ValueError):
        sanitize_state_key("///")
    with pytest.raises(ValueError):
        sanitize_state_key("..")


def test_save_load_roundtrip():
    key = "proj/db"
    save_state(key, {"database_name": key, "remote_version": 7, "x": 1})
    loaded = load_state(key)
    assert loaded["database_name"] == key
    assert loaded["remote_version"] == 7
    assert loaded["x"] == 1


def test_state_persists_under_paradox_home():
    key = "proj/db"
    save_state(key, {"database_name": key})
    assert (config.CONFIG_DIR / "proj__db.sync.json").exists()


def test_default_state_has_new_fields():
    s = load_state("never-seen/db")
    assert s["dirty"] is False
    assert s["offline"] is False
    assert s["remote_version"] is None
    assert s["last_sync"] is None
    assert s["last_local_hash"] is None


def test_mark_dirty_is_dirty_clear_dirty():
    key = "proj/db"
    assert is_dirty(key) is False
    mark_dirty(key)
    assert is_dirty(key) is True
    clear_dirty(key)
    assert is_dirty(key) is False


def test_offline_set_is():
    key = "proj/db"
    assert is_offline(key) is False
    set_offline(key, True)
    assert is_offline(key) is True
    set_offline(key, False)
    assert is_offline(key) is False


def test_dirty_and_offline_are_persisted():
    key = "proj/db"
    mark_dirty(key)
    set_offline(key, True)
    reloaded = load_state(key)
    assert reloaded["dirty"] is True
    assert reloaded["offline"] is True


def test_get_sync_status_shape():
    key = "proj/db"
    set_remote_version(key, 3, file_hash="abc123")
    set_last_local_hash(key, "def456")
    mark_dirty(key)
    set_offline(key, True)

    status = get_sync_status(key)
    assert set(status.keys()) == {
        "database_name",
        "remote_version",
        "remote_hash",
        "last_sync",
        "last_local_hash",
        "dirty",
        "offline",
    }
    assert status["database_name"] == key
    assert status["remote_version"] == 3
    assert status["remote_hash"] == "abc123"
    assert status["last_local_hash"] == "def456"
    assert status["last_sync"] is not None
    assert status["dirty"] is True
    assert status["offline"] is True


def test_get_sync_status_defaults():
    status = get_sync_status("brand-new/db")
    assert status["remote_version"] is None
    assert status["remote_hash"] is None
    assert status["last_sync"] is None
    assert status["last_local_hash"] is None
    assert status["dirty"] is False
    assert status["offline"] is False


def test_existing_functions_still_work():
    key = "proj/db"
    set_remote_version(key, 5, file_hash="hash5")
    assert get_remote_version(key) == 5
    set_last_local_hash(key, "hashX")
    assert load_state(key)["last_local_hash"] == "hashX"
