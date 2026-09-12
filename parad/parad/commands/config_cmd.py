"""parad config — View and update configuration."""

import click
from parad.config import load_config, set_config_value, save_config


@click.group("config")
def config_group():
    """Manage parad configuration."""
    pass


@config_group.command("show")
def config_show():
    """Show current configuration with secret-bearing fields redacted."""
    import json

    config = load_config().model_dump()
    config["database_url"] = "<redacted>" if config.get("database_url") else ""
    config.setdefault("encryption", {})["passphrase"] = "<redacted>" if config.get("encryption", {}).get("passphrase") else ""
    config.setdefault("sync", {})["api_key"] = "<redacted>" if config.get("sync", {}).get("api_key") else ""
    click.echo(json.dumps(config, indent=2))


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """Set a config value: parad config set sync.api_key pk_xxx"""
    set_config_value(key, value)
    shown = "<redacted>" if any(part.lower() in {"database_url", "api_key", "passphrase", "password", "token"} for part in key.split(".")) else value
    click.echo(f"✓ Set {key} = {shown}")
