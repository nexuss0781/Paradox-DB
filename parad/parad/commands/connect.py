"""parad connect — Connect to a database and start auto-sync."""

import click
from pathlib import Path
from parad.config import load_config, save_config, CONFIG_DIR, gateway_db_name
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError
from parad.state import get_remote_version, set_remote_version, set_last_local_hash
from parad.watcher import is_running, start_daemon


@click.command()
@click.argument("name")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
@click.option("--no-watch", is_flag=True, help="Don't start auto-sync daemon")
def connect(name: str, passphrase: str, no_watch: bool):
    """Connect to an existing database and enable auto-sync.

    If the database exists locally, uses it. Otherwise pulls from gateway.
    """
    config = load_config()
    db_path = CONFIG_DIR / f"{name}.db"
    db_name = name  # gateway name without .db

    # Update config to point to this DB
    config.database_path = str(db_path)
    save_config(config)

    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)

    # Check if DB exists locally
    if db_path.exists():
        click.echo(f"✓ Found local database: {db_path}")
        # Initialize state if needed
        if get_remote_version(db_name) is None:
            # Push local to gateway
            engine = Engine(str(db_path), passphrase)
            engine.open()
            raw = engine.get_raw_bytes()
            engine.close()
            try:
                result = gw.upload(db_name, raw)
                set_remote_version(db_name, result.version)
                click.echo(f"✓ Synced to gateway v{result.version}")
            except GatewayError as e:
                click.echo(f"⚠ Sync failed: {e}")
    else:
        # Try to pull from gateway
        click.echo(f"Local DB not found, pulling from gateway...")
        try:
            dl = gw.download(db_name)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            from parad.crypto import encrypt_file
            db_path.write_bytes(encrypt_file(dl.bytes, passphrase))
            if dl.version is not None:
                set_remote_version(db_name, dl.version)
            set_last_local_hash(db_name, _file_hash(db_path))
            ver = f"v{dl.version}" if dl.version else "latest"
            click.echo(f"✓ Pulled {ver} ({len(dl.bytes)} bytes)")
        except GatewayError as e:
            click.echo(f"✗ Could not pull from gateway: {e}")
            click.echo("  Create a new DB with: parad init " + name)
            raise SystemExit(1)

    # Start watch daemon
    if not no_watch:
        if is_running():
            click.echo("  Auto-sync daemon already running.")
        else:
            click.echo("Starting auto-sync daemon...")
            import multiprocessing
            p = multiprocessing.Process(target=start_daemon, args=(passphrase,), daemon=True)
            p.start()
            click.echo(f"✓ Auto-sync enabled (pid={p.pid})")

    click.echo(f"\nConnected to {db_name}. Ready to use.")
    click.echo("  Run: parad shell, parad insert, parad select, etc.")


def _file_hash(path: Path) -> str:
    import hashlib
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()
