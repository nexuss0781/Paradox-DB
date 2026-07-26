"""parad CLI entry point."""

import os
import click

@click.group()
@click.version_option(package_name="parad")
def main():
    """parad — Encrypted local-first SQLite with Telegram cloud sync."""
    pass


# Import and register all commands
from parad.commands.init import init
from parad.commands.sync import push, pull, sync
from parad.commands.status import status, versions, rollback
from parad.commands.query import exec_cmd, insert, select, update, delete
from parad.commands.shell import shell
from parad.commands.config_cmd import config_group
from parad.commands.watch import watch_cmd
from parad.commands.connect import connect

main.add_command(connect)
main.add_command(init)
main.add_command(push)
main.add_command(pull)
main.add_command(sync)
main.add_command(status)
main.add_command(versions)
main.add_command(rollback)
main.add_command(exec_cmd, name="exec")
main.add_command(insert)
main.add_command(select)
main.add_command(update)
main.add_command(delete)
main.add_command(shell)
main.add_command(watch_cmd)
main.add_command(config_group)
