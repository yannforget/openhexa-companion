"""`hexa webapps` commands."""

from pathlib import Path

import click

from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._local import check_targets, local_path, save_stream
from openhexa.companion.cli._output import (
    cut,
    fetch_limit,
    format_size,
    format_time,
    json_option,
    limit_option,
    nest,
    note,
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
# Scopes that let the webapp change the workspace for the person viewing it.
WRITE_SCOPES = frozenset({"DATASETS_WRITE", "FILES_WRITE", "PIPELINES_RUN"})

source_argument = click.argument(
    "source_dir",
    metavar="[SOURCE]",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)


@click.group()
def webapps() -> None:
    """Read and publish webapps.

    WEBAPP is the slug of a webapp, as shown by `hexa webapps list`.
    """


@webapps.command("list")
@limit_option("webapps")
@json_option
@pass_client
def list_webapps(client: LazyClient, limit: int | None, as_json: bool) -> None:
    """List the webapps of the workspace."""
    library = client.get()
    items = cut(
        library.webapps.list(limit=fetch_limit(limit)),
        limit,
        empty=f"No webapps in workspace {library.workspace}",
    )
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
    """Show a webapp, the files of the version it serves and its latest versions.

    With --json, prints the webapp with its `files` and latest `versions`.
    """
    library = client.get()
    item = library.webapps.get(webapp)
    files = library.webapps.files(webapp) if item.type == "STATIC" else []
    versions = library.webapps.versions(webapp, limit=5) if item.type == "STATIC" else []
    if as_json:
        print_json(nest(item, files=files, versions=versions))
        return
    click.echo(f"{item.name} ({item.slug}, {item.type})")
    click.echo(f"URL: {item.url}")
    click.echo(f"Public: {'yes' if item.public else 'no'}")
    if item.version:
        latest = " (the latest)" if versions and versions[0].id == item.version else ""
        click.echo(f"Serving version: {item.version}{latest}")
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
@click.option(
    "--version",
    help="ID of a version, as shown by `hexa webapps versions list`. Defaults to the one "
    "the webapp serves.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the files in. Defaults to WEBAPP.",
)
@click.option("--force", is_flag=True, help="Replace local files that already exist.")
@json_option
@pass_client
def download(
    client: LazyClient,
    *,
    webapp: str,
    version: str | None,
    output: Path | None,
    force: bool,
    as_json: bool,
) -> None:
    """Download the files of a static webapp.

    With --json, prints the files written, with their `local_path`.
    """
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
    if as_json:
        print_json(
            [
                {"path": file.path, "size": file.size, "local_path": str(target)}
                for file, target in targets
            ]
        )


@webapps.command()
@click.argument("name")
@source_argument
@click.option("-d", "--description", help="What the webapp shows.")
@click.option("--public", is_flag=True, help="Let anyone open it without signing in.")
@click.option(
    "--scope",
    "scopes",
    multiple=True,
    type=click.Choice(SCOPES, case_sensitive=False),
    help="An OpenHEXA operation the webapp may perform for the person viewing it. Repeat "
    "for each scope.",
)
@json_option
@pass_client
def create(
    client: LazyClient,
    *,
    name: str,
    source_dir: Path,
    description: str | None,
    public: bool,
    scopes: tuple[str, ...],
    as_json: bool,
) -> None:
    """Create a static webapp named NAME from the site in SOURCE and print its slug.

    SOURCE is a directory with index.html at its root, such as a build output, the
    current directory by default. OpenHEXA derives the slug from NAME. See `hexa
    webapps publish` for the files sent. With --json, prints the webapp with the paths
    `uploaded`.
    """
    result = client.get().webapps.create(
        name,
        source_dir,
        description=description,
        public=public,
        scopes=[scope.upper() for scope in scopes],
    )
    _report(result, as_json=as_json)


@webapps.command()
@click.argument("webapp")
@source_argument
@click.option("-m", "--message", help="What changed in this version.")
@json_option
@pass_client
def publish(
    client: LazyClient,
    *,
    webapp: str,
    source_dir: Path,
    message: str | None,
    as_json: bool,
) -> None:
    """Publish the site in SOURCE as a new version of WEBAPP and serve it.

    SOURCE is a directory with index.html at its root, such as a build output, the
    current directory by default. The webapp ends up with exactly the files of SOURCE;
    files no longer there are removed. Hidden files and directories (such as .env and
    .git), node_modules and __pycache__ are not sent. The new version is live at once,
    even if an earlier version was served again with `hexa webapps update --version`.
    With --json, prints the webapp with the paths `uploaded` and `deleted`.
    """
    result = client.get().webapps.publish(webapp, source_dir, message=message)
    _report(result, as_json=as_json)


@webapps.command()
@click.argument("webapp")
@click.option("--name", help="Rename the webapp. Its slug and URL do not change.")
@click.option("-d", "--description", help="What the webapp shows.")
@click.option(
    "--public/--private",
    default=None,
    help="Let anyone open it without signing in, or only workspace members.",
)
@click.option(
    "--scope",
    "scopes",
    multiple=True,
    type=click.Choice(SCOPES, case_sensitive=False),
    help="An OpenHEXA operation the webapp may perform for the person viewing it. Replaces "
    "the current scopes; repeat for each scope.",
)
@click.option("--no-scopes", is_flag=True, help="Remove every scope.")
@click.option(
    "--version",
    help="Serve this version of a static webapp: its ID or the start of it, as shown by "
    "`hexa webapps versions list`.",
)
@json_option
@pass_client
def update(
    client: LazyClient,
    *,
    webapp: str,
    name: str | None,
    description: str | None,
    public: bool | None,
    scopes: tuple[str, ...],
    no_scopes: bool,
    version: str | None,
    as_json: bool,
) -> None:
    """Change the settings of WEBAPP, or the version it serves.

    --version serves an earlier version, to roll back a publication; newer versions are
    kept, so you can serve them again. `hexa webapps publish` publishes a new version and
    serves it. Making the webapp public or giving it scopes that change data prints a
    warning. Only workspace editors and admins may change a webapp. With --json, prints
    the webapp.
    """
    if scopes and no_scopes:
        raise click.UsageError("Give either --scope or --no-scopes, not both")
    new_scopes = [scope.upper() for scope in scopes] if scopes else [] if no_scopes else None
    if all(value is None for value in (name, description, public, new_scopes, version)):
        raise click.UsageError(
            "Give at least one of --name, --description, --public, --private, --scope, "
            "--no-scopes or --version"
        )
    library = client.get()
    before = library.webapps.get(webapp)
    item = library.webapps.update(
        webapp,
        name=name,
        description=description,
        public=public,
        scopes=new_scopes,
        version=version,
    )
    click.echo(f"Updated webapp {item.slug}", err=True)
    if item.public and not before.public:
        note(f"Warning: anyone can now open {item.url} without signing in")
    added = sorted(WRITE_SCOPES.intersection(item.scopes).difference(before.scopes))
    if added:
        note(
            f"Warning: {item.slug} may now {', '.join(added)} for the person viewing it"
            + (", and anyone can open it" if item.public else "")
        )
    if version is not None:
        note(f"Serving version {item.version}")
    if as_json:
        print_json(item)


@webapps.command()
@click.argument("webapp")
@yes_option
@json_option
@pass_client
def delete(client: LazyClient, webapp: str, yes: bool, as_json: bool) -> None:
    """Delete a webapp. With --json, prints the webapp."""
    library = client.get()
    item = library.webapps.get(webapp)
    confirm(f"Delete webapp '{item.slug}' served at {item.url}", yes=yes)
    library.webapps.delete(webapp)
    click.echo(f"Deleted webapp {item.slug}", err=True)
    if as_json:
        print_json(item)


@webapps.group()
def versions() -> None:
    """List the versions of a static webapp."""


@versions.command("list")
@click.argument("webapp")
@limit_option("versions", default=20)
@json_option
@pass_client
def list_versions(client: LazyClient, webapp: str, limit: int, as_json: bool) -> None:
    """List the versions of a static webapp, newest first.

    Give a version's ID to `hexa webapps download --version`.
    """
    items = cut(
        client.get().webapps.versions(webapp, limit=fetch_limit(limit)),
        limit,
        empty=f"Webapp {webapp} has no versions",
    )
    if as_json:
        print_json(items)
        return
    print_table(
        [(v.id, format_time(v.created_at), v.author, v.message) for v in items],
        ("version", "published", "author", "message"),
    )


def _report(result: Publication, *, as_json: bool) -> None:
    if as_json:
        print_json(nest(result.webapp, uploaded=result.uploaded, deleted=result.deleted))
        return
    click.echo(
        f"Published {len(result.uploaded)} file(s), removed {len(result.deleted)}: "
        f"{result.webapp.url}",
        err=True,
    )
    click.echo(result.webapp.slug)
