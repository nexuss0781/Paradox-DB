"""parad init — Create a new encrypted database and push to gateway."""

import click
from pathlib import Path
from parad.config import load_config, save_config, config_dir, gateway_db_name, set_config_value
from parad.connection import db_state_key, generate_url, redact_url
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError
from parad.commands.auth import authenticate_api_key
from parad.state import set_remote_version, set_last_local_hash
from parad.watcher import is_running


def _ensure_auth(config):
    """Auto-authenticate: prompt for an API key if no valid token.

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
    api_key = click.prompt("Paradox or Nexuss API key", hide_input=True)

    try:
        resolved, _ = authenticate_api_key(config, api_key)
        gw.api_key = resolved.api_key
        if gw.api_key:
            config.sync.api_key = gw.api_key
            click.echo("✓ Authentication successful")
        else:
            raise click.ClickException("Authentication succeeded but no Paradox API key was received")
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
@click.option("--print-database-url", is_flag=True, help="Print the full secret-bearing DATABASE_URL")
def init(name: str, passphrase: str, gateway: str | None, project: str | None, do_watch: bool, print_database_url: bool):
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
    db_path = config_dir() / f"{name}.db"
    config.database_path = str(db_path)
    config.project_id = project_id
    config.project_name = project_name
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

    state_key = db_state_key(name, project_name or None)
    try:
        result = gw.upload(name, raw, database_id=database_id, project_id=project_id)
        ver = result.version
        set_remote_version(state_key, ver)
        set_last_local_hash(state_key, _file_hash(db_path))
        click.echo(f"✓ Pushed to gateway v{ver}")
    except GatewayError as e:
        click.echo(f"⚠ Push failed: {e}")

    # Step 6: Save config and publish the canonical URL only after creation succeeds.
    canonical_url = generate_url(
        name,
        passphrase,
        config.sync.gateway_url,
        project_name,
        token=config.sync.api_key,
    )
    config.database_url = canonical_url
    save_config(config)

    click.echo(f"\n✓ Database ready: {db_path}")
    click.echo(f"  Project:  {project_name} ({project_id})")
    click.echo(f"  Database: {name} ({database_id})")
    click.echo(f"  DATABASE_URL: {canonical_url if print_database_url else redact_url(canonical_url)}")

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
