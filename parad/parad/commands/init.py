"""parad init — Create a new encrypted database and register with gateway."""

import click
from pathlib import Path
from parad.config import load_config, save_config, CONFIG_DIR
from parad.engine import Engine
from parad.gateway import GatewayClient
from parad.state import set_remote_version
from parad.watcher import is_running


@click.command()
@click.argument("name")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
@click.option("--gateway", envvar="PARADOX_GATEWAY_URL", default=None)
@click.option("--watch", "do_watch", is_flag=True, help="Start auto-sync daemon after init")
def init(name: str, passphrase: str, gateway: str | None, do_watch: bool):
    """Create a new encrypted database and register with gateway."""
    config = load_config()
    
    if gateway:
        config.sync.gateway_url = gateway
    
    db_path = CONFIG_DIR / f"{name}.db"
    config.database_path = str(db_path)
    
    # Create local encrypted database
    engine = Engine(str(db_path), passphrase)
    engine.open(create=True)
    engine.create_tables()
    engine.close()
    
    click.echo(f"✓ Created encrypted database: {db_path}")
    
    # Auto-register if no API key
    if not config.sync.api_key:
        gw = GatewayClient(config.sync.gateway_url)
        try:
            result = gw.register()
            config.sync.api_key = result.api_key
            save_config(config)
            click.echo(f"✓ Registered with gateway (user: {result.user_id})")
            click.echo(f"  API key: {result.api_key}")
        except Exception as e:
            click.echo(f"⚠ Gateway registration failed: {e}")
            click.echo("  You can register later with: parad config set sync.api_key <key>")
    else:
        click.echo("  Gateway already configured")
    
    # Push initial DB to gateway
    db_path_resolved = Path(config.database_path).expanduser()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    db_name = Path(config.database_path).name

    engine = Engine(str(db_path_resolved), passphrase)
    engine.open()
    raw = engine.get_raw_bytes()
    engine.close()

    try:
        result = gw.upload(db_name, raw)
        set_remote_version(db_name, result.version)
        click.echo(f"✓ Pushed to gateway v{result.version}")
    except Exception as e:
        click.echo(f"⚠ Initial push failed: {e}")

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
