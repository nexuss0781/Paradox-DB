"""parad project — List, create, manage projects."""

import json
import click
from parad.config import load_config
from parad.gateway import GatewayClient, GatewayError


@click.group("project")
def project_group():
    """Manage projects."""
    pass


@project_group.command("list")
def list_projects():
    """List all projects."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        projects = gw.list_projects()
        if not projects:
            click.echo("No projects found.")
            return
        for p in projects:
            db_count = p.get("database_count", 0)
            click.echo(f"  {p['name']}  ({db_count} databases)")
            click.echo(f"    ID: {p['id']}")
            if p.get("description"):
                click.echo(f"    {p['description']}")
            click.echo()
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)


@project_group.command("create")
@click.argument("name")
@click.option("--description", "-d", default="")
def create_project(name, description):
    """Create a new project."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        result = gw.create_project(name, description)
        click.echo(f"Created project: {result['name']}")
        click.echo(f"  ID: {result['id']}")
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)


@project_group.command("get")
@click.argument("name_or_id")
def get_project(name_or_id):
    """Get project details."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        projects = gw.list_projects()
        project = None
        for p in projects:
            if p["id"] == name_or_id or p["name"] == name_or_id:
                project = p
                break
        if not project:
            click.echo(f"Project not found: {name_or_id}")
            raise SystemExit(1)
        click.echo(json.dumps(project, indent=2))
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)


@project_group.command("delete")
@click.argument("name_or_id")
@click.confirmation_option(prompt="Are you sure?")
def delete_project(name_or_id):
    """Delete a project and all its databases."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        projects = gw.list_projects()
        project = None
        for p in projects:
            if p["id"] == name_or_id or p["name"] == name_or_id:
                project = p
                break
        if not project:
            click.echo(f"Project not found: {name_or_id}")
            raise SystemExit(1)
        gw.delete_project(project["id"])
        click.echo(f"Deleted project: {project['name']}")
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)
