"""parad auth — Register, login, and manage authentication."""

import click
from click import ClickException
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
        token = result.get("access_token", "")
        if token:
            set_config_value("sync.api_key", token)
        click.echo(f"✓ Registered as {result.get('username', '')} ({result.get('email', '')})")
        click.echo(f"  User ID: {result.get('user_id', '')}")
    except GatewayError as e:
        click.echo(f"✗ Registration failed: {e}")
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
        token = result.get("access_token", "")
        if token:
            set_config_value("sync.api_key", token)
        click.echo(f"✓ Logged in as {result.get('username', '')}")
    except GatewayError as e:
        click.echo(f"✗ Login failed: {e}")
        raise SystemExit(1)


def _ensure_auth(gw):
    """Ensure user is authenticated. Prompt for credentials if needed.

    In non-interactive mode (CI/cloud), raises an error instead of prompting.
    """
    if gw.api_key:
        try:
            gw.get_me()
            return
        except GatewayError:
            pass

    # Non-interactive mode: raise clear error
    import sys
    if not sys.stdin.isatty():
        raise ClickException(
            "Not authenticated. Set PARADOX_API_KEY environment variable or run 'parad auth login' first."
        )

    click.echo("Authentication required.")
    email = click.prompt("Email")
    password = click.prompt("Password", hide_input=True)

    try:
        result = gw.login(email, password)
        token = result.get("access_token") or result.get("api_key")
        if token:
            set_config_value("sync.api_key", token)
            click.echo("✓ Authentication successful")
        else:
            raise ClickException("Login succeeded but no token received")
    except GatewayError as e:
        raise ClickException(f"Authentication failed: {e}")


def _require_auth(gw):
    """Require authentication. Raises ClickException if auth fails."""
    _ensure_auth(gw)


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
