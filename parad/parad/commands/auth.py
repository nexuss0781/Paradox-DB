"""parad auth — Register, login, and manage authentication."""

import click
from parad.config import load_config, save_config, set_config_value
from parad.gateway import GatewayClient, GatewayError


@click.group("auth")
def auth_group():
    """Manage authentication."""
    pass


@auth_group.command("register")
@click.option("--email", prompt=True)
@click.option("--username", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
def register(email, username, password):
    """Register a new account."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url)
    try:
        result = gw.register_email(email, username, password)
        set_config_value("sync.api_key", result.access_token)
        click.echo(f"Registered as {result.username} ({result.email})")
        click.echo(f"  User ID: {result.user_id}")
    except GatewayError as e:
        click.echo(f"Registration failed: {e}")
        raise SystemExit(1)


@auth_group.command("login")
@click.option("--email", prompt=True)
@click.option("--password", prompt=True, hide_input=True)
def login(email, password):
    """Login with email and password."""
    config = load_config()
    gw = GatewayClient(config.sync.gateway_url)
    try:
        result = gw.login(email, password)
        set_config_value("sync.api_key", result.access_token)
        click.echo(f"Logged in as {result.username}")
    except GatewayError as e:
        click.echo(f"Login failed: {e}")
        raise SystemExit(1)


@auth_group.command("status")
def auth_status():
    """Show current authentication status."""
    config = load_config()
    if not config.sync.api_key:
        click.echo("Not logged in.")
        return
    gw = GatewayClient(config.sync.gateway_url, config.sync.api_key)
    try:
        me = gw.get_me()
        click.echo(f"Logged in as: {me.get('username', 'unknown')} ({me.get('email', '')})")
        click.echo(f"User ID: {me.get('id', 'unknown')}")
    except GatewayError:
        click.echo("Token expired or invalid. Run: parad auth login")
