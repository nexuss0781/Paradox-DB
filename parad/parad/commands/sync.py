"""parad push/pull/sync — Upload and download databases."""

import hashlib
import click
from pathlib import Path
from parad.config import load_config, gateway_db_name
from parad.connection import db_state_key
from parad.crypto import encrypt_file
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError
from parad.state import get_remote_version, set_remote_version, set_last_local_hash


def _get_db_name(config) -> str:
    return gateway_db_name(config.database_path)


def _state_key(config) -> str:
    return db_state_key(_get_db_name(config), getattr(config, "project_name", "") or None)


def _local_hash(db_path: Path) -> str:
    return hashlib.sha256(db_path.read_bytes()).hexdigest()


@click.command("push")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
def push(passphrase: str):
    """Push the local database to the gateway."""
    config = load_config()
    db_path = Path(config.database_path).expanduser()

    if not db_path.exists():
        click.echo(f"✗ Database not found: {db_path}")
        raise SystemExit(1)

    db_name = _get_db_name(config)
    state_key = _state_key(config)
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)

    # Read raw bytes (plaintext)
    engine = Engine(str(db_path), passphrase)
    engine.open()
    raw = engine.get_raw_bytes()
    engine.close()

    # Send tracked version for conflict detection
    version = get_remote_version(state_key)

    try:
        result = gw.upload(
            db_name,
            raw,
            version=version if version else 0,
            database_id=config.database_id,
            project_id=config.project_id,
        )
    except GatewayError as e:
        if e.status_code == 409:
            click.echo("⚠ Conflict (409) — pulling remote, re-pushing local (local-wins)")
            local_raw = raw
            try:
                dl = gw.download(db_name, database_id=config.database_id, project_id=config.project_id)
                db_path.write_bytes(encrypt_file(dl.bytes, passphrase))
                if dl.version is not None:
                    set_remote_version(state_key, dl.version)
                version = get_remote_version(state_key)
                result = gw.upload(
                    db_name,
                    local_raw,
                    version=version if version else 0,
                    database_id=config.database_id,
                    project_id=config.project_id,
                )
                # our bytes won — write them back so the local file matches
                db_path.write_bytes(encrypt_file(local_raw, passphrase))
            except GatewayError as e2:
                click.echo(f"✗ Local-wins re-push failed: {e2}")
                raise SystemExit(1)
        else:
            click.echo(f"✗ Push failed: {e}")
            raise SystemExit(1)

    set_remote_version(state_key, result.version)
    set_last_local_hash(state_key, _local_hash(db_path))
    click.echo(f"✓ Pushed {db_name} v{result.version} (msg={result.message_id})")


@click.command("pull")
@click.argument("version", required=False, type=int)
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
def pull(version: int | None, passphrase: str):
    """Pull database from gateway. Optionally specify a version."""
    config = load_config()
    db_path = Path(config.database_path).expanduser()
    db_name = _get_db_name(config)
    state_key = _state_key(config)

    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)

    try:
        dl = gw.download(db_name, version, database_id=config.database_id, project_id=config.project_id)
    except GatewayError as e:
        click.echo(f"✗ Pull failed: {e}")
        raise SystemExit(1)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(encrypt_file(dl.bytes, passphrase))

    # Update sync state
    if dl.version is not None:
        set_remote_version(state_key, dl.version)
    set_last_local_hash(state_key, _local_hash(db_path))

    ver_str = f"v{dl.version}" if dl.version else "latest"
    click.echo(f"✓ Pulled {db_name} {ver_str} ({len(dl.bytes)} bytes)")


@click.command("sync")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
def sync(passphrase: str):
    """Push local changes, then pull latest from gateway."""
    config = load_config()
    db_path = Path(config.database_path).expanduser()
    db_name = _get_db_name(config)
    state_key = _state_key(config)

    if not db_path.exists():
        click.echo(f"✗ Database not found: {db_path}")
        raise SystemExit(1)

    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)

    # Push
    engine = Engine(str(db_path), passphrase)
    engine.open()
    raw = engine.get_raw_bytes()
    engine.close()

    version = get_remote_version(state_key)

    try:
        result = gw.upload(
            db_name,
            raw,
            version=version if version else 0,
            database_id=config.database_id,
            project_id=config.project_id,
        )
        set_remote_version(state_key, result.version)
        set_last_local_hash(state_key, _local_hash(db_path))
        click.echo(f"✓ Pushed v{result.version}")
    except GatewayError as e:
        if e.status_code == 409:
            click.echo("⚠ Conflict (409) — pulling remote, re-pushing local (local-wins)")
            local_raw = raw
            try:
                dl = gw.download(db_name, database_id=config.database_id, project_id=config.project_id)
                db_path.write_bytes(encrypt_file(dl.bytes, passphrase))
                if dl.version is not None:
                    set_remote_version(state_key, dl.version)
                version = get_remote_version(state_key)
                result = gw.upload(
                    db_name,
                    local_raw,
                    version=version if version else 0,
                    database_id=config.database_id,
                    project_id=config.project_id,
                )
                db_path.write_bytes(encrypt_file(local_raw, passphrase))
                set_remote_version(state_key, result.version)
                set_last_local_hash(state_key, _local_hash(db_path))
                click.echo(f"✓ Pushed v{result.version} (local-wins)")
            except GatewayError as e2:
                click.echo(f"✗ Local-wins re-push failed: {e2}")
        else:
            click.echo(f"⚠ Push failed: {e}")

    # Pull
    try:
        dl = gw.download(db_name, database_id=config.database_id, project_id=config.project_id)
        db_path.write_bytes(encrypt_file(dl.bytes, passphrase))
        if dl.version is not None:
            set_remote_version(state_key, dl.version)
        set_last_local_hash(state_key, _local_hash(db_path))
        click.echo(f"✓ Pulled latest ({len(dl.bytes)} bytes)")
    except GatewayError as e:
        click.echo(f"⚠ Pull failed: {e}")
