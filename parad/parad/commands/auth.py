"""parad auth — authenticate with Paradox or Nexuss Auth API keys."""

import click
from click import ClickException
from parad.config import load_config, save_config, set_config_value
from parad.gateway import GatewayClient, GatewayError


@click.group("auth")
def auth_group():
    """Manage authentication."""
    pass


def authenticate_api_key(config, api_key: str) -> tuple[GatewayClient, dict]:
    """Validate a Paradox key or exchange a Nexuss key without persisting nxa_."""
    supplied = api_key.strip()
    if not supplied:
        raise ClickException("Provide a Paradox pk_ key or a Nexuss Auth nxa_ key")
    gateway = GatewayClient(config.sync.gateway_url)
    if supplied.startswith("nxa_"):
        result = gateway.exchange_nexuss_api_key(supplied)
        resolved_key = result.get("api_key", "")
        if not resolved_key.startswith("pk_"):
            raise ClickException("Nexuss Auth exchange did not return a Paradox API key")
        gateway.api_key = resolved_key
    else:
        gateway.api_key = supplied
        result = {"api_key": supplied, **gateway.get_me()}
    set_config_value("sync.api_key", gateway.api_key)
    config.sync.api_key = gateway.api_key
    return gateway, result


@auth_group.command("register")
def register():
    """Explain the passwordless Nexuss Auth registration flow."""
    raise ClickException(
        "Paradox accounts are created through Nexuss Auth. Sign in with Google in the Paradox web flow, "
        "then run 'parad auth login --api-key <nexuss nxa_ key>'."
    )


@auth_group.command("login")
@click.option("--api-key", envvar="PARADOX_API_KEY", help="Paradox pk_ key or Nexuss Auth nxa_ key")
def login(api_key: str | None):
    """Authenticate using a Paradox key or exchange a Nexuss Auth key."""
    config = load_config()
    supplied = api_key or click.prompt("Paradox or Nexuss API key", hide_input=True)
    try:
        _, result = authenticate_api_key(config, supplied)
        click.echo(f"✓ Logged in as {result.get('username', '')}")
    except GatewayError as e:
        click.echo(f"✗ Login failed: {e}")
        raise SystemExit(1)


def _ensure_auth(gw):
    """Ensure user is authenticated. Prompt for an API key if needed.

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
    api_key = click.prompt("Paradox or Nexuss API key", hide_input=True)

    try:
        config = load_config()
        resolved, _ = authenticate_api_key(config, api_key)
        gw.api_key = resolved.api_key
        if gw.api_key:
            click.echo("✓ Authentication successful")
        else:
            raise ClickException("Authentication succeeded but no Paradox API key was received")
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
        click.echo("API key invalid. Run: parad auth login")
