"""parad init — Create a new encrypted database and push to gateway."""

import click
from pathlib import Path
from parad.config import load_config, save_config, CONFIG_DIR, gateway_db_name, set_config_value
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError
from parad.state import set_remote_version, set_last_local_hash
from parad.watcher import is_running


def _ensure_auth(config):
    """Auto-authenticate: prompt for credentials if no valid token."""
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)

    if gw.api_key:
        try:
            gw.get_me()
            return gw
        except GatewayError:
            pass

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


def _find_or_create_project(gw, project_name: str | None) -> tuple[str, str]:
    """Find or create a project. Returns (project_id, project_name)."""
    projects = gw.list_projects()

    if project_name:
        for p in projects:
            if p.get("name") == project_name:
                return p["id"], p["name"]
        # Not found — create it
        click.echo(f"  Creating project '{project_name}'...")
        result = gw.create_project(project_name)
        return result["id"], result["name"]

    if projects:
        p = projects[0]
        click.echo(f"  Using project: {p.get('name', p['id'])}")
        return p["id"], p["name"]

    click.echo("  No projects found. Creating 'default'...")
    result = gw.create_project("default")
    return result["id"], result["name"]


def _find_or_create_database(gw, project_id: str, db_name: str) -> str:
    """Find or create a database in the project. Returns database_id."""
    databases = gw.list_databases(project_id)

    for db in databases:
        if db.get("name") == db_name:
            click.echo(f"  Using existing database: {db_name}")
            return db["id"]

    click.echo(f"  Creating database '{db_name}'...")
    result = gw.create_database(project_id, db_name)
    return result["id"]


@click.command()
@click.argument("name")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
@click.option("--gateway", envvar="PARADOX_GATEWAY_URL", default=None)
@click.option("--project", default=None, help="Project name to use (creates if not found)")
@click.option("--watch", "do_watch", is_flag=True, help="Start auto-sync daemon after init")
def init(name: str, passphrase: str, gateway: str | None, project: str | None, do_watch: bool):
    """Create a new encrypted database and push to gateway.

    Handles everything in one step: auth, project/database setup, local DB creation, and push.
    If project or database don't exist on gateway, they are created automatically.
    """
    config = load_config()

    if gateway:
        config.sync.gateway_url = gateway

    # Step 1: Auto-authenticate
    gw = _ensure_auth(config)

    # Step 2: Find or create project
    click.echo(f"\nSetting up project...")
    project_id, project_name = _find_or_create_project(gw, project)

    # Step 3: Find or create database
    click.echo(f"Setting up database...")
    database_id = _find_or_create_database(gw, project_id, name)

    # Step 4: Create local encrypted database
    db_path = CONFIG_DIR / f"{name}.db"
    config.database_path = str(db_path)
    config.project_id = project_id
    config.database_id = database_id

    engine = Engine(str(db_path), passphrase)
    engine.open(create=True)
    engine.create_tables()
    engine.close()
    click.echo(f"✓ Created encrypted database: {db_path}")

    # Step 5: Push to gateway
    db_path_resolved = Path(config.database_path).expanduser()
    engine = Engine(str(db_path_resolved), passphrase)
    engine.open()
    raw = engine.get_raw_bytes()
    engine.close()

    try:
        result = gw.upload(name, raw)
        ver = result.version
        set_remote_version(name, ver)
        set_last_local_hash(name, _file_hash(db_path))
        click.echo(f"✓ Pushed to gateway v{ver}")
    except GatewayError as e:
        click.echo(f"⚠ Push failed: {e}")

    # Step 6: Save config
    save_config(config)

    click.echo(f"\n✓ Database ready: {db_path}")
    click.echo(f"  Project:  {project_name} ({project_id})")
    click.echo(f"  Database: {name} ({database_id})")

    # Step 7: Optionally start daemon
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
