"""Canonical database URL retrieval commands."""

import json

import click

from parad.config import recover_canonical_database_url, register_canonical_database_url
from parad.connection import redact_url


def _emit_url(name: str | None, print_database_url: bool, json_mode: bool) -> None:
    database_url = recover_canonical_database_url(name)
    displayed = database_url if print_database_url else redact_url(database_url)
    if json_mode:
        click.echo(json.dumps({"database_url": displayed}))
    else:
        click.echo(f"DATABASE_URL: {displayed}")


def _register_url(database_url: str, print_database_url: bool, json_mode: bool) -> None:
    registered = register_canonical_database_url(database_url)
    displayed = registered if print_database_url else redact_url(registered)
    if json_mode:
        click.echo(json.dumps({"registered": True, "database_url": displayed}))
    else:
        click.echo(f"Registered DATABASE_URL: {displayed}")


def _handle_url_command(
    name: str | None,
    database_url: str | None,
    print_database_url: bool,
    json_mode: bool,
) -> None:
    if name == "register":
        if not database_url:
            raise click.UsageError("Usage: parad url register <canonical-url>")
        _register_url(database_url, print_database_url, json_mode)
        return
    if database_url:
        raise click.UsageError("Only `register` accepts a second argument")
    _emit_url(name, print_database_url, json_mode)


@click.command("url")
@click.argument("name", required=False)
@click.argument("database_url", required=False)
@click.option("--print-database-url", is_flag=True, help="Print the full secret-bearing DATABASE_URL")
@click.option("--json", "json_mode", is_flag=True, help="Print JSON output")
def url_command(name: str | None, database_url: str | None, print_database_url: bool, json_mode: bool) -> None:
    """Retrieve or explicitly register the canonical database_url."""
    _handle_url_command(name, database_url, print_database_url, json_mode)


@click.command("database-url")
@click.argument("name", required=False)
@click.argument("database_url", required=False)
@click.option("--print-database-url", is_flag=True, help="Print the full secret-bearing DATABASE_URL")
@click.option("--json", "json_mode", is_flag=True, help="Print JSON output")
def database_url_command(name: str | None, database_url: str | None, print_database_url: bool, json_mode: bool) -> None:
    """Alias for ``parad url``."""
    _handle_url_command(name, database_url, print_database_url, json_mode)
