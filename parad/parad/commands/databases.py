"""parad db — Manage databases within projects."""

import json
import click
from parad.config import load_config
from parad.gateway import GatewayClient, GatewayError


def _resolve_project(gw, name_or_id):
    """Find a project by name or ID."""
    projects = gw.list_projects()
    for p in projects:
        if p["id"] == name_or_id or p["name"] == name_or_id:
            return p
    return None


def _resolve_database(gw, database_id):
    """Get database details."""
    return gw.get_database(database_id)


@click.group("db")
def db_group():
    """Manage databases."""
    pass


@db_group.command("list")
@click.argument("project_name_or_id")
def list_databases(project_name_or_id):
    """List databases in a project."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        project = _resolve_project(gw, project_name_or_id)
        if not project:
            click.echo(f"Project not found: {project_name_or_id}")
            raise SystemExit(1)
        databases = gw.list_databases(project["id"])
        if not databases:
            click.echo("No databases in this project.")
            return
        for d in databases:
            click.echo(f"  {d['name']}  (v{d['latest_version']})")
            click.echo(f"    ID: {d['id']}")
            if d.get("description"):
                click.echo(f"    {d['description']}")
            click.echo()
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)


@db_group.command("create")
@click.argument("project_name_or_id")
@click.argument("db_name")
@click.option("--description", "-d", default="")
def create_database(project_name_or_id, db_name, description):
    """Create a database in a project."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        project = _resolve_project(gw, project_name_or_id)
        if not project:
            click.echo(f"Project not found: {project_name_or_id}")
            raise SystemExit(1)
        result = gw.create_database(project["id"], db_name, description)
        click.echo(f"Created database: {result['name']}")
        click.echo(f"  ID: {result['id']}")
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)


@db_group.command("get")
@click.argument("database_id")
def get_database(database_id):
    """Get database details."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        result = gw.get_database(database_id)
        click.echo(json.dumps(result, indent=2))
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)


@db_group.command("delete")
@click.argument("database_id")
@click.confirmation_option(prompt="Are you sure?")
def delete_database(database_id):
    """Delete a database and all its versions."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        gw.delete_database(database_id)
        click.echo("Database deleted")
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)
