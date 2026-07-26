"""parad status/versions/rollback — View sync status and manage versions."""

import hashlib
import click
from pathlib import Path
from parad.config import load_config
from parad.crypto import encrypt_file
from parad.gateway import GatewayClient, GatewayError
from parad.state import load_state


@click.command("status")
def status():
    """Show local vs remote sync status."""
    config = load_config()
    db_path = Path(config.database_path).expanduser()
    db_name = db_path.name
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)

    try:
        result = gw.status()
    except GatewayError as e:
        click.echo(f"✗ Status check failed: {e}")
        raise SystemExit(1)

    # Load local sync state
    local_state = load_state(db_name)
    local_version = local_state.get("remote_version")
    last_sync = local_state.get("last_sync")

    # Compute local file hash
    local_hash = None
    if db_path.exists():
        local_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()[:16]

    click.echo(f"User: {result.user_id}\n")
    if not result.databases:
        click.echo("No databases found on gateway.")
        if db_path.exists():
            click.echo(f"  Local DB exists: {db_path}")
        return

    for db in result.databases:
        click.echo(f"  {db.name}")
        click.echo(f"    Remote:   v{db.latest_version}  msg={db.latest_message_id}")

        # Find matching local state
        is_current_db = (db.name == db_name)
        if is_current_db and local_version is not None:
            click.echo(f"    Local:    v{local_version}")
            if local_version == db.latest_version:
                click.echo(f"    Status:   ✓ In sync")
            else:
                click.echo(f"    Status:   ⚠ Out of sync (local v{local_version} vs remote v{db.latest_version})")
        elif is_current_db:
            click.echo(f"    Local:    (not tracked)")
            click.echo(f"    Status:   ⚠ Run 'parad push' to sync")

        if local_hash:
            click.echo(f"    Hash:     {local_hash}")
        if db.last_sync_at:
            click.echo(f"    Last sync: {db.last_sync_at}")
        click.echo()


@click.command("versions")
@click.option("--name", "-n", default=None, help="Database name")
def versions(name: str | None):
    """List all remote versions."""
    config = load_config()
    if not name:
        name = Path(config.database_path).name

    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)

    try:
        result = gw.versions(name)
    except GatewayError as e:
        click.echo(f"✗ Versions query failed: {e}")
        raise SystemExit(1)

    if not result.versions:
        click.echo(f"No versions found for {name}.")
        return

    click.echo(f"Versions for {name}:\n")
    for v in result.versions:
        size_str = f" ({v.size_bytes} bytes)" if v.size_bytes else ""
        click.echo(f"  v{v.version}{size_str}  msg={v.message_id}")
        if v.uploaded_at:
            click.echo(f"    {v.uploaded_at}")


@click.command("rollback")
@click.argument("version", type=int)
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
def rollback(version: int, passphrase: str):
    """Rollback to a previous version."""
    config = load_config()
    db_name = Path(config.database_path).name
    db_path = Path(config.database_path).expanduser()

    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)

    try:
        result = gw.rollback(db_name, version)
        click.echo(f"✓ Rolled back to v{result.rolled_back_to} (new msg={result.new_message_id})")
    except GatewayError as e:
        click.echo(f"✗ Rollback failed: {e}")
        raise SystemExit(1)

    # Pull the rolled-back version
    try:
        dl = gw.download(db_name)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(encrypt_file(dl.bytes, passphrase))
        click.echo(f"✓ Pulled rolled-back version")
    except GatewayError as e:
        click.echo(f"⚠ Pull after rollback failed: {e}")
