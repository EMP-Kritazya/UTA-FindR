"""Command-line entry points: python -m ingest.cli <command>"""
from pathlib import Path

import typer
from alembic import command
from alembic.config import Config

app = typer.Typer(help="UTAFindR ingestion commands.", no_args_is_help=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

@app.callback()
def main():
    """UTAFindR ingestion commands."""
    # Having a callback tells Typer this app has subcommands --


@app.command("init-db")
def init_db():
    """Create/upgrade all tables via Alembic migrations (never create_all)."""
    # Building a CongFig from Project_ROOT/"alembic.ini"
    cfg = Config(str(PROJECT_ROOT/"alembic.ini"))
    migration_dir = PROJECT_ROOT/"migrations"
    cfg.set_main_option("script_location", str(migration_dir))

    command.upgrade(cfg, "head")

    typer.echo("Database Migrations applied successfully")


if __name__ == "__main__":
    app()