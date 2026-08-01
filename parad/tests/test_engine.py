"""Hermetic tests for parad.engine (no network)."""

import pytest

from parad.crypto import SQLITE_MAGIC, DecryptionError


def test_create_insert_close_reopen_preserves_data(make_engine):
    engine = make_engine("main.db")
    assert engine.is_open is False

    engine.open(create=True)
    assert engine.is_open is True
    engine.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    engine.insert("items", {"name": "alpha"})
    engine.insert("items", {"name": "beta"})
    engine.close()
    assert engine.is_open is False

    engine2 = make_engine("main.db")
    engine2.open()
    rows = engine2.select("items")
    assert rows == [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]
    engine2.close()


def test_open_create_when_file_missing(make_engine):
    engine = make_engine("new.db")
    conn = engine.open(create=True)
    assert conn is not None
    assert engine.is_open
    engine.close()
    assert engine.db_path.exists()
    assert engine.db_path.stat().st_size > 0


def test_empty_file_with_create_is_treated_as_fresh(make_engine):
    # A 0-byte file (e.g. left by `touch` or a partially-initialized
    # connection string) must not be treated as corrupt when create=True.
    engine = make_engine("half.db")
    engine.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine.db_path.write_bytes(b"")
    assert engine.db_path.stat().st_size == 0

    engine.open(create=True)
    engine.execute("CREATE TABLE t (x TEXT)")
    engine.insert("t", {"x": "hello"})
    engine.close()

    engine2 = make_engine("half.db")
    engine2.open()
    assert engine2.select("t") == [{"x": "hello"}]
    engine2.close()


def test_empty_file_without_create_raises_clear_error(make_engine):
    engine = make_engine("empty.db")
    engine.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine.db_path.write_bytes(b"")
    with pytest.raises(DecryptionError):
        engine.open()
    assert engine.is_open is False
    assert engine._tmp_path is None
    engine.close()  # must not throw on a failed-open state


def test_corrupt_file_raises_clear_error_and_no_half_open(make_engine):
    engine = make_engine("corrupt.db")
    engine.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine.db_path.write_bytes(b"\xde\xad\xbe\xef" * 32)

    with pytest.raises(DecryptionError) as excinfo:
        engine.open()
    assert "Invalid passphrase or corrupt database file" in str(excinfo.value)
    assert engine.is_open is False
    assert engine._tmp_path is None
    engine.close()  # no-op, must not throw


def test_wrong_passphrase_on_open_raises(make_engine):
    engine = make_engine("locked.db")
    engine.open(create=True)
    engine.execute("CREATE TABLE t (x TEXT)")
    engine.insert("t", {"x": "secret"})
    engine.close()

    bad = make_engine("locked.db")
    bad.passphrase = "wrong-passphrase"
    with pytest.raises(DecryptionError):
        bad.open()
    assert bad.is_open is False
    assert bad._tmp_path is None
    bad.close()


def test_close_is_idempotent(make_engine):
    engine = make_engine("idem.db")
    engine.open(create=True)
    engine.execute("CREATE TABLE t (x TEXT)")
    engine.close()
    engine.close()  # second close: nothing open, must be a no-op
    engine.close()
    assert engine.is_open is False
    assert engine._tmp_path is None


def test_get_raw_bytes_is_plaintext_sqlite(make_engine):
    engine = make_engine("raw.db")
    engine.open(create=True)
    engine.execute("CREATE TABLE t (x TEXT)")
    raw = engine.get_raw_bytes()
    assert raw.startswith(SQLITE_MAGIC)
    engine.close()

    # When closed, get_raw_bytes decrypts the on-disk file.
    engine2 = make_engine("raw.db")
    assert engine2.get_raw_bytes().startswith(SQLITE_MAGIC)


def test_open_twice_returns_same_connection(make_engine):
    engine = make_engine("twice.db")
    engine.open(create=True)
    conn = engine._conn
    assert engine.open() is conn
    engine.close()


def test_execute_on_closed_engine_raises(make_engine):
    engine = make_engine("closed.db")
    with pytest.raises(RuntimeError):
        engine.execute("SELECT 1")
    with pytest.raises(RuntimeError):
        engine.select("t")
