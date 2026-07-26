"""parad version — List versions and compare."""

import click
from parad.config import load_config
from parad.gateway import GatewayClient, GatewayError


@click.group("version")
def version_group():
    """Manage database versions."""
    pass


@version_group.command("list")
@click.argument("database_id")
def list_versions(database_id):
    """List all versions for a database."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        versions = gw.list_versions(database_id)
        if not versions:
            click.echo("No versions found.")
            return
        for v in versions:
            size_str = f" ({v['file_size']} bytes)" if v.get("file_size") else ""
            click.echo(f"  v{v['version_number']}{size_str}  {v.get('created_at', '')[:19]}")
            if v.get("notes"):
                click.echo(f"    {v['notes']}")
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)


@version_group.command("diff")
@click.argument("database_id")
@click.argument("version_a", type=int)
@click.argument("version_b", type=int)
def diff_versions(database_id, version_a, version_b):
    """Compare two versions of a database."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        result = gw.diff_versions(database_id, version_a, version_b)
        if result.get("identical"):
            click.echo(f"v{version_a} and v{version_b} are identical")
        else:
            click.echo(f"v{version_a} vs v{version_b}:")
            click.echo(f"  Hash A: {result['hash_a'][:16]}...")
            click.echo(f"  Hash B: {result['hash_b'][:16]}...")
            click.echo(f"  Size A: {result['size_a']} bytes")
            click.echo(f"  Size B: {result['size_b']} bytes")
            click.echo(f"  Different: yes")
    except GatewayError as e:
        click.echo(f"{e}")
        raise SystemExit(1)
