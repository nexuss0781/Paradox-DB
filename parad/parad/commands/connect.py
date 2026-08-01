"""parad connect — Connect to a database and start auto-sync."""

import click
from pathlib import Path
from parad.config import load_config, save_config, config_dir, gateway_db_name, set_config_value
from parad.connection import db_state_key
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError
from parad.state import get_remote_version, set_remote_version, set_last_local_hash
from parad.watcher import is_running, get_daemon_pid, start_daemon


def _ensure_auth(config):
    """Auto-authenticate: prompt for credentials if no valid token.

    In non-interactive mode (CI/cloud), raises an error instead of prompting.
    """
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)

    if gw.api_key:
        try:
            gw.get_me()
            return gw
        except GatewayError:
            pass

    # Non-interactive mode: raise clear error
    import sys
    if not sys.stdin.isatty():
        raise click.ClickException(
            "Not authenticated. Set PARADOX_API_KEY environment variable or run 'parad auth login' first."
        )

    click.echo("Authentication required.")
    email = click.prompt("Email")
    password = click.prompt("Password", hide_input=True)

    try:
        result = gw.login(email, password)
        token = result.get("access_token") or result.get("api_key")
        if token:
            set_config_value("sync.api_key", token)
            config.sync.api_key = token
            click.echo("✓ Authentication successful")
        else:
            raise click.ClickException("Login succeeded but no token received")
    except GatewayError as e:
        raise click.ClickException(f"Authentication failed: {e}")

    return gw


def _resolve_database_by_name(gw, config, db_name: str) -> tuple[str, str, str] | None:
    """Find project_id, project_name, database_id by scanning projects/databases. Returns (project_id, project_name, database_id) or None."""
    projects = gw.list_projects()
    for p in projects:
        databases = gw.list_databases(p["id"])
        for db in databases:
            if db.get("name") == db_name:
                return p["id"], p.get("name", p["id"]), db["id"]
    return None


def _file_hash(path: Path) -> str:
    import hashlib
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@click.command()
@click.argument("name")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
@click.option("--no-watch", is_flag=True, help="Don't start auto-sync daemon")
@click.option("--watch", "do_watch", is_flag=True, help="Keep running until Ctrl+C")
def connect(name: str, passphrase: str, no_watch: bool, do_watch: bool):
    """Connect to an existing database and enable auto-sync.

    Finds the database on gateway by name, syncs locally, and starts the watcher daemon.
    """
    config = load_config()
    db_path = config_dir() / f"{name}.db"

    # Step 1: Auto-authenticate
    gw = _ensure_auth(config)

    # Step 2: Resolve database on gateway
    click.echo(f"Looking up database '{name}' on gateway...")
    resolved = _resolve_database_by_name(gw, config, name)

    if resolved:
        project_id, project_name, database_id = resolved
        config.project_id = project_id
        config.project_name = project_name
        config.database_id = database_id
    else:
        click.echo(f"✗ Database '{name}' not found on gateway.")
        click.echo(f"  Create it with: parad init {name}")
        raise SystemExit(1)

    # Step 3: Set config
    config.database_path = str(db_path)
    save_config(config)

    state_key = db_state_key(name, config.project_name or None)

    # Step 4: Ensure local file exists
    if not db_path.exists():
        click.echo("Local file not found, pulling from gateway...")
        try:
            dl = gw.download(name, database_id=config.database_id, project_id=config.project_id)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            from parad.crypto import encrypt_file
            db_path.write_bytes(encrypt_file(dl.bytes, passphrase))
            if dl.version is not None:
                set_remote_version(state_key, dl.version)
            set_last_local_hash(state_key, _file_hash(db_path))
            ver = f"v{dl.version}" if dl.version else "latest"
            click.echo(f"✓ Pulled {ver} ({len(dl.bytes)} bytes)")
        except GatewayError as e:
            click.echo(f"✗ Could not pull from gateway: {e}")
            click.echo(f"  Create a new DB with: parad init {name}")
            raise SystemExit(1)
    else:
        click.echo(f"✓ Found local database: {db_path}")
        # Sync state if needed
        if get_remote_version(state_key) is None:
            engine = Engine(str(db_path), passphrase)
            engine.open()
            raw = engine.get_raw_bytes()
            engine.close()
            try:
                result = gw.upload(name, raw, database_id=config.database_id, project_id=config.project_id)
                set_remote_version(state_key, result.version)
                set_last_local_hash(state_key, _file_hash(db_path))
                click.echo(f"✓ Synced to gateway v{result.version}")
            except GatewayError as e:
                click.echo(f"⚠ Sync failed: {e}")

    # Step 5: Start watcher daemon
    daemon_pid = None
    if not no_watch:
        if is_running():
            daemon_pid = get_daemon_pid()
            click.echo(f"  Auto-sync daemon already running (pid={daemon_pid}).")
        else:
            import multiprocessing
            p = multiprocessing.Process(target=start_daemon, args=(passphrase,), daemon=True)
            p.start()
            daemon_pid = p.pid
            click.echo(f"✓ Auto-sync daemon started (pid={daemon_pid})")

    # Step 6: Print connection info
    local_ver = get_remote_version(state_key)
    ver_str = f"v{local_ver}" if local_ver else "unknown"
    url = f"parad://local/{name}?passphrase={passphrase}"
    daemon_status = f"running (PID {daemon_pid})" if daemon_pid else "not running"

    click.echo(f"""
✓ Connected to {name}
  Local:  {db_path}
  Remote: {ver_str}
  Status: In sync
  URL:    {url}
  Daemon: {daemon_status}
""")

    # Step 7: Keep running if --watch
    if do_watch and daemon_pid:
        click.echo("Press Ctrl+C to stop...")
        try:
            import signal
            signal.pause()
        except (KeyboardInterrupt, SystemExit):
            click.echo("\nShutting down...")
