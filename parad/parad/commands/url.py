"""Canonical database URL retrieval commands."""

import json

import click

from parad.config import get_canonical_database_url
from parad.connection import redact_url


def _emit_url(name: str | None, print_database_url: bool, json_mode: bool) -> None:
    database_url = get_canonical_database_url(name)
    displayed = database_url if print_database_url else redact_url(database_url)
    if json_mode:
        click.echo(json.dumps({"database_url": displayed}))
    else:
        click.echo(f"DATABASE_URL: {displayed}")


@click.command("url")
@click.argument("name", required=False)
@click.option("--print-database-url", is_flag=True, help="Print the full secret-bearing DATABASE_URL")
@click.option("--json", "json_mode", is_flag=True, help="Print JSON output")
def url_command(name: str | None, print_database_url: bool, json_mode: bool) -> None:
    """Retrieve the canonical database_url from env/config or legacy fields."""
    _emit_url(name, print_database_url, json_mode)


@click.command("database-url")
@click.argument("name", required=False)
@click.option("--print-database-url", is_flag=True, help="Print the full secret-bearing DATABASE_URL")
@click.option("--json", "json_mode", is_flag=True, help="Print JSON output")
def database_url_command(name: str | None, print_database_url: bool, json_mode: bool) -> None:
    """Alias for ``parad url``."""
    _emit_url(name, print_database_url, json_mode)
