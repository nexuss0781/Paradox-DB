"""parad backup — Create and manage database backups."""

import click
from parad.config import load_config
from parad.gateway import GatewayClient, GatewayError


@click.group("backup")
def backup_group():
    """Manage database backups."""
    pass


@backup_group.command("create")
@click.argument("database_id")
@click.argument("name")
@click.option("--notes", "-n", default="")
def create_backup(database_id, name, notes):
    """Create a backup at the current version."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        result = gw.create_backup(database_id, name, notes)
        click.echo(f"Backup created: {result['name']}")
        click.echo(f"  Version: v{result['version_number']}")
        click.echo(f"  ID: {result['id']}")
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)


@backup_group.command("list")
@click.argument("database_id")
def list_backups(database_id):
    """List all backups for a database."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        backups = gw.list_backups(database_id)
        if not backups:
            click.echo("No backups found.")
            return
        for b in backups:
            click.echo(f"  {b['name']}  (v{b['version_number']})  {b.get('created_at', '')[:19]}")
            click.echo(f"    ID: {b['id']}")
            if b.get("notes"):
                click.echo(f"    {b['notes']}")
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)


@backup_group.command("restore")
@click.argument("database_id")
@click.argument("backup_id")
@click.confirmation_option(prompt="This will create a new version. Continue?")
def restore_backup(database_id, backup_id):
    """Restore a database from a backup."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        result = gw.restore_backup(database_id, backup_id)
        click.echo(f"Restored: {result['detail']}")
        click.echo(f"  New version: v{result['version']}")
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)
