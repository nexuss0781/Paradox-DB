"""parad watch — Auto-sync daemon commands."""

import click
from parad.watcher import (
    start_daemon,
    is_running,
    stop_daemon,
    get_daemon_pid,
)


@click.command("watch")
@click.option("--passphrase", envvar="PARADOX_PASSPHRASE", default="default")
@click.option("--stop", "do_stop", is_flag=True, help="Stop the running daemon")
@click.option("--status", "do_status", is_flag=True, help="Show daemon status")
def watch_cmd(passphrase: str, do_stop: bool, do_status: bool):
    """Auto-sync daemon — watches local DB and syncs automatically."""
    if do_stop:
        if stop_daemon():
            click.echo("✓ Watcher daemon stopped.")
        else:
            click.echo("No watcher daemon is running.")
        return

    if do_status:
        pid = get_daemon_pid()
        if pid:
            click.echo(f"✓ Watcher daemon running (pid={pid})")
        else:
            click.echo("No watcher daemon running.")
        return

    if is_running():
        click.echo("✗ Watcher daemon already running. Use 'parad watch --stop' first.")
        raise SystemExit(1)

    click.echo("Starting auto-sync watcher...")
    click.echo("  Press Ctrl+C to stop.\n")
    start_daemon(passphrase=passphrase)
