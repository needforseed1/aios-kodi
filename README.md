# AIOStreams for Kodi

Kodi video add-on for browsing Stremio-compatible metadata catalogs and playing AIOStreams sources.

It supports:

- AIOMetadata catalogs, search, posters, metadata, and IMDb-backed IDs.
- Public Cinemeta fallback when AIOMetadata is not configured.
- AIOStreams source lookup and direct HTTP/HTTPS playback.
- Movie, series, season, episode, search, resume, and view-mode workflows.

Torrent infohash-only streams are shown as unsupported because Kodi cannot play them directly without a separate torrent playback layer.

## Install From Repository

Add the repository URL as a Kodi file source:

```text
https://needforseed1.github.io/aios-kodi/
```

Then install the repository add-on:

1. Open `Settings -> File manager -> Add source`.
2. Enter the URL above and name it `AIOStreams`.
3. Open `Settings -> Add-ons -> Install from zip file`.
4. Select `AIOStreams -> repository.aiostreams -> repository.aiostreams-0.1.0.zip`.
5. Open `Install from repository -> AIOStreams Repository -> Video add-ons`.
6. Install `AIOStreams for Kodi`.

When the hosted repository is updated, Kodi can update the add-on through the normal add-on update flow.

Direct zip fallback:

[repository.aiostreams-0.1.0.zip](https://needforseed1.github.io/aios-kodi/repository.aiostreams/repository.aiostreams-0.1.0.zip)

## Configure

Open the add-on settings and paste the full AIOStreams install URL. The URL usually ends with `manifest.json`:

```text
https://example.com/stremio/<uuid>/<config>/manifest.json
```

AIOMetadata is optional. If it is blank, the add-on uses public Cinemeta for catalogs, search, and metadata.

Kodi settings take precedence, but the add-on can also read a credentials file:

```text
<Kodi userdata>/addon_data/plugin.video.aiostreams/credentials.json
```

Use `Settings -> Credentials file location` inside the add-on to create or show the exact path. The file format is:

```json
{
  "aiostreams_url": "https://example.com/stremio/<uuid>/<config>/manifest.json",
  "aiometadata_url": ""
}
```

For best matching, use a metadata provider that returns IMDb IDs. Cinemeta works as the default fallback for standard movie and series IDs.

## Build The Repository

For local development, generate the installable Kodi repository tree:

```bash
python3 tools/build_repo.py --clean
```

This writes:

```text
repo/addons.xml
repo/addons.xml.md5
repo/plugin.video.aiostreams/plugin.video.aiostreams-<version>.zip
repo/repository.aiostreams/repository.aiostreams-0.1.0.zip
```

The default repository URL is:

```text
https://needforseed1.github.io/aios-kodi/
```

To publish somewhere else, pass the public base URL when building:

```bash
python3 tools/build_repo.py --clean --base-url https://example.com/kodi/
```

Host the contents of `repo/` at that URL. Kodi reads `addons.xml`, verifies `addons.xml.md5`, and downloads add-on zips from the subdirectories.

## Publish With GitHub Pages

The `repository` workflow builds and deploys the repository tree to GitHub Pages on pushes to `main` and from manual workflow runs. The published repository zip is available at:

```text
https://needforseed1.github.io/aios-kodi/repository.aiostreams/repository.aiostreams-0.1.0.zip
```

## Development

Run the local checks:

```bash
python3 -m compileall -q addon.py service.py resources/lib
python3 -m pytest -q
```

Build artifacts in `repo/` and `builds/` are ignored by git.
