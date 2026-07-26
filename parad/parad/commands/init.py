"""parad init — Create a new encrypted database and push to gateway."""

import click
from pathlib import Path
from parad.config import load_config, save_config, CONFIG_DIR, gateway_db_name
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError
from parad.state import set_remote_version, set_last_local_hash
from parad.watcher import is_running


@click.command()
@click.argument("name")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
@click.option("--gateway", envvar="PARADOX_GATEWAY_URL", default=None)
@click.option("--watch", "do_watch", is_flag=True, help="Start auto-sync daemon after init")
def init(name: str, passphrase: str, gateway: str | None, do_watch: bool):
    """Create a new encrypted database and push to gateway.

    You must be authenticated first: parad auth register (or parad auth login)
    You must create the database on gateway first: parad db create <project> <name>
    """
    config = load_config()

    if gateway:
        config.sync.gateway_url = gateway

    if not config.sync.api_key:
        click.echo("✗ Not authenticated. Run: parad auth register (or parad auth login)")
        raise SystemExit(1)

    db_path = CONFIG_DIR / f"{name}.db"
    db_name = name  # gateway name without .db
    config.database_path = str(db_path)

    # Create local encrypted database
    engine = Engine(str(db_path), passphrase)
    engine.open(create=True)
    engine.create_tables()
    engine.close()

    click.echo(f"✓ Created encrypted database: {db_path}")

    # Push to gateway
    db_path_resolved = Path(config.database_path).expanduser()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)

    engine = Engine(str(db_path_resolved), passphrase)
    engine.open()
    raw = engine.get_raw_bytes()
    engine.close()

    try:
        result = gw.upload(db_name, raw)
        set_remote_version(db_name, result.version)
        set_last_local_hash(db_name, _file_hash(db_path))
        click.echo(f"✓ Pushed to gateway v{result.version}")
    except GatewayError as e:
        click.echo(f"⚠ Push failed: {e}")
        click.echo("  Make sure the database exists on gateway: parad db create <project> " + name)

    save_config(config)
    click.echo(f"\nDatabase ready at: {db_path}")

    if do_watch:
        if is_running():
            click.echo("  Auto-sync daemon already running.")
        else:
            click.echo("Starting auto-sync daemon...")
            import multiprocessing
            from parad.watcher import start_daemon
            p = multiprocessing.Process(target=start_daemon, args=(passphrase,), daemon=True)
            p.start()
            click.echo(f"✓ Auto-sync enabled (pid={p.pid})")


def _file_hash(path: Path) -> str:
    import hashlib
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()
