# Static webapps

Read this before building or publishing a webapp. The full guide, with the fields each
scope allows and example apps, is at <https://docs.openhexa.com/static-webapps/>.

A static webapp is a directory of HTML, CSS and JavaScript with `index.html` at its
root, such as a build output. There is no server-side code, and no fallback route for
single-page apps: use hash routing.

## Reading OpenHEXA data

- The app calls `fetch("/graphql/", {method: "POST", ...})` on its own origin. OpenHEXA
  answers as the person viewing the app, limited to the app's scopes. Never put a token
  or an `Authorization` header in the app.
- Scopes are set with `--scope` on `hexa webapps create` or `update`. Grant only what the
  app needs; `*_write` and `pipelines_run` let it change data, so ask first. A query
  selecting any field outside the scopes is refused with a 403 that names the field.
- Public apps can't call `/graphql/`. To show workspace data publicly, bundle it as a
  file, for example produced by a pipeline.
- The database is reached only through saved queries (`executeSavedQuery`), never raw
  SQL. Dataset files are read by fetching the `downloadUrl` of a dataset version file,
  without credentials.
- OpenHEXA sets `window.OPENHEXA` with `workspaceSlug` and `webappSlug`; read them rather
  than hard-coding slugs.

## Developing locally

Add the dev bridge to `index.html`, with the instance URL from `.env`:

```html
<script src="OPENHEXA_URL/webapps/dev.js"
        data-workspace-slug="WORKSPACE" data-webapp-slug="WEBAPP"></script>
```

It only acts on `localhost` and `file://` pages, so it can stay in the published app.
The user clicks **Connect** and approves in the browser, then `/graphql/` calls reach the
real workspace with the webapp's scopes. The webapp must exist first, so create it
before connecting. Calls are real, including those that change data.

## Publishing

`hexa webapps create` and `hexa webapps publish` send the whole directory except hidden
files, `node_modules` and `__pycache__`, and the new version is served at once. Publish
only the build output, without source maps or data the viewers shouldn't get.
`publish` replaces the files of the previous version; `hexa webapps update --version`
serves an earlier one. After publishing, open the app's URL to check it, and report what
was checked in a browser separately from what the command reported.
