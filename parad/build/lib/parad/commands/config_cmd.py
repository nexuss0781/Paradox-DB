"""parad config — View and update configuration."""

import click
from parad.config import load_config, set_config_value, save_config


@click.group("config")
def config_group():
    """Manage parad configuration."""
    pass


@config_group.command("show")
def config_show():
    """Show current configuration."""
    import json
    config = load_config()
    click.echo(json.dumps(config.model_dump(), indent=2))


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """Set a config value: parad config set sync.api_key pk_xxx"""
    set_config_value(key, value)
    click.echo(f"✓ Set {key} = {value}")
