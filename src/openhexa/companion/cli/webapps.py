"""`hexa webapps` commands."""

from pathlib import Path

import click

from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._local import check_targets, local_path, save_stream
from openhexa.companion.cli._output import (
    format_size,
    format_time,
    json_option,
    print_json,
    print_table,
)
from openhexa.companion.cli._prompt import confirm, yes_option
from openhexa.companion.webapps import Publication

# Operations a webapp may perform for the person viewing it.
SCOPES = (
    "USER_READ",
    "DATASETS_READ",
    "DATASETS_WRITE",
    "FILES_READ",
    "FILES_WRITE",
    "DATABASE_READ",
    "PIPELINES_READ",
    "PIPELINES_RUN",
)

source_option = click.option(
    "--source",
    "source_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    show_default=True,
    help="Directory with index.html at its root, such as a build output.",
)


@click.group()
def webapps() -> None:
    """Read and publish webapps.

    WEBAPP is the slug of a webapp, as shown by `hexa webapps list`.
    """


@webapps.command("list")
@json_option
@pass_client
def list_webapps(client: LazyClient, as_json: bool) -> None:
    """List the webapps of the workspace."""
    items = client.get().webapps.list()
    if as_json:
        print_json(items)
        return
    print_table(
        [(w.slug, w.type, "yes" if w.public else "no", w.url) for w in items],
        ("webapp", "type", "public", "url"),
    )


@webapps.command()
@click.argument("webapp")
@json_option
@pass_client
def show(client: LazyClient, webapp: str, as_json: bool) -> None:
    """Show a webapp, its files and its latest versions."""
    library = client.get()
    item = library.webapps.get(webapp)
    files = library.webapps.files(webapp) if item.type == "STATIC" else []
    versions = library.webapps.versions(webapp, limit=5) if item.type == "STATIC" else []
    if as_json:
        print_json({"webapp": item, "files": files, "versions": versions})
        return
    click.echo(f"{item.name} ({item.slug}, {item.type})")
    click.echo(f"URL: {item.url}")
    click.echo(f"Public: {'yes' if item.public else 'no'}")
    if item.scopes:
        click.echo(f"Scopes: {', '.join(item.scopes)}")
    if item.description:
        click.echo(item.description)
    if files:
        click.echo()
        print_table([(f.path, format_size(f.size)) for f in files], ("file", "size"))
    if versions:
        click.echo()
        print_table(
            [(v.id[:10], format_time(v.created_at), v.author, v.message) for v in versions],
            ("version", "published", "author", "message"),
        )


@webapps.command()
@click.argument("webapp")
@click.option("--version", help="ID of a version. Defaults to the latest one.")
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the files in. Defaults to WEBAPP.",
)
@click.option("--force", is_flag=True, help="Replace local files that already exist.")
@pass_client
def download(
    client: LazyClient, *, webapp: str, version: str | None, output: Path | None, force: bool
) -> None:
    """Download the files of a static webapp."""
    target_dir = output or Path(webapp)
    files = client.get().webapps.files(webapp, version=version, content=True)
    unreadable = [f.path for f in files if f.content is None]
    if unreadable:
        raise click.ClickException(
            f"Files too large to download through the API: {', '.join(unreadable)}"
        )
    targets = [(f, local_path(target_dir, f.path)) for f in files]
    check_targets((target for _, target in targets), force=force)
    for file, target in targets:
        save_stream([file.content or b""], target)
    click.echo(f"Downloaded {len(targets)} file(s) to {target_dir}", err=True)


@webapps.command()
@click.argument("webapp", required=False)
@source_option
@click.option("-m", "--message", help="Description of the change.")
@click.option(
    "--create",
    "create_name",
    metavar="NAME",
    help="Create a new static webapp named NAME instead of publishing to WEBAPP.",
)
@click.option("--description", help="With --create: what the webapp shows.")
@click.option(
    "--public", is_flag=True, help="With --create: let anyone open it without signing in."
)
@click.option(
    "--scope",
    "scopes",
    multiple=True,
    type=click.Choice(SCOPES, case_sensitive=False),
    help="With --create: an OpenHEXA operation the webapp may perform for the person "
    "viewing it. Repeat for each scope.",
)
@json_option
@pass_client
def publish(
    client: LazyClient,
    *,
    webapp: str | None,
    source_dir: Path,
    message: str | None,
    create_name: str | None,
    description: str | None,
    public: bool,
    scopes: tuple[str, ...],
    as_json: bool,
) -> None:
    """Publish the site in --source as the new content of WEBAPP.

    The webapp ends up with exactly the files of --source; files no longer there are
    removed. Hidden files and directories (such as .env and .git), node_modules and
    __pycache__ are not sent. The change is live at once.
    """
    if (webapp is None) == (create_name is None):
        raise click.UsageError("Give either WEBAPP or --create NAME")
    if webapp is not None and (description or public or scopes):
        raise click.UsageError("--description, --public and --scope need --create")
    library = client.get()
    if webapp is None:
        result = library.webapps.create(
            create_name or "",
            source_dir,
            description=description,
            public=public,
            scopes=[scope.upper() for scope in scopes],
        )
    else:
        result = library.webapps.publish(webapp, source_dir, message=message)
    _report(result, as_json=as_json)


@webapps.command()
@click.argument("webapp")
@yes_option
@pass_client
def delete(client: LazyClient, webapp: str, yes: bool) -> None:
    """Delete a webapp."""
    library = client.get()
    item = library.webapps.get(webapp)
    confirm(f"Delete webapp '{item.slug}' served at {item.url}", yes=yes)
    library.webapps.delete(webapp)
    click.echo(f"Deleted webapp {item.slug}", err=True)


def _report(result: Publication, *, as_json: bool) -> None:
    if as_json:
        print_json(result)
        return
    click.echo(
        f"Published {len(result.uploaded)} file(s), removed {len(result.deleted)}: "
        f"{result.webapp.url}",
        err=True,
    )
    click.echo(result.webapp.slug)
