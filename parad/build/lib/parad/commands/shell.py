"""parad shell — Interactive SQL REPL."""

import click
from parad.config import load_config
from parad.engine import Engine


@click.command("shell")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
def shell(passphrase: str):
    """Interactive SQL shell for the local database."""
    config = load_config()
    
    click.echo(f"parad shell — {config.database_path}")
    click.echo("Type SQL commands, or 'quit' to exit.\n")
    
    with Engine(config.database_path, passphrase) as engine:
        while True:
            try:
                line = input("parad> ").strip()
            except (EOFError, KeyboardInterrupt):
                click.echo("\nBye.")
                break
            
            if not line:
                continue
            if line.lower() in ("quit", "exit", "\\q"):
                click.echo("Bye.")
                break
            
            try:
                rows = engine.execute(line)
                if rows:
                    import json
                    for row in rows:
                        click.echo(json.dumps(dict(row), default=str))
                else:
                    click.echo("OK")
            except Exception as e:
                click.echo(f"Error: {e}")
