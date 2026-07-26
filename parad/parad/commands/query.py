"""parad exec/insert/select/update/delete — Local SQL operations."""

import json
import click
from parad.config import load_config
from parad.engine import Engine


@click.command("exec")
@click.argument("sql")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
def exec_cmd(sql: str, passphrase: str):
    """Execute raw SQL on the local database."""
    config = load_config()
    with Engine(config.database_path, passphrase) as engine:
        rows = engine.execute(sql)
        if rows:
            for row in rows:
                click.echo(json.dumps(dict(row), default=str))
        else:
            click.echo("OK")


@click.command("insert")
@click.argument("table")
@click.argument("data_json")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
def insert(table: str, data_json: str, passphrase: str):
    """Insert a row: parad insert <table> '{"col": "val"}'."""
    config = load_config()
    data = json.loads(data_json)
    with Engine(config.database_path, passphrase) as engine:
        rowid = engine.insert(table, data)
        click.echo(f"Inserted rowid={rowid}")


@click.command("select")
@click.argument("table")
@click.argument("where_json", required=False)
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
def select(table: str, where_json: str | None, passphrase: str):
    """Query rows: parad select <table> ['{"col": "val"}']."""
    config = load_config()
    where = json.loads(where_json) if where_json else None
    with Engine(config.database_path, passphrase) as engine:
        rows = engine.select(table, where)
        if not rows:
            click.echo("No rows found.")
            return
        for row in rows:
            click.echo(json.dumps(dict(row), default=str))


@click.command("update")
@click.argument("table")
@click.argument("set_json")
@click.argument("where_json")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
def update(table: str, set_json: str, where_json: str, passphrase: str):
    """Update rows: parad update <table> '{"col":"val"}' '{"id":1}'."""
    config = load_config()
    set_data = json.loads(set_json)
    where = json.loads(where_json)
    with Engine(config.database_path, passphrase) as engine:
        changes = engine.update(table, set_data, where)
        click.echo(f"Updated {changes} row(s)")


@click.command("delete")
@click.argument("table")
@click.argument("where_json")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
def delete(table: str, where_json: str, passphrase: str):
    """Delete rows: parad delete <table> '{"id":1}'."""
    config = load_config()
    where = json.loads(where_json)
    with Engine(config.database_path, passphrase) as engine:
        changes = engine.delete(table, where)
        click.echo(f"Deleted {changes} row(s)")
