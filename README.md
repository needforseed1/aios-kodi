# AIOStreams for Kodi

Kodi video add-on for browsing Stremio-compatible metadata catalogs and playing AIOStreams sources.

It supports:

- AIOMetadata catalogs, search, posters, metadata, and IMDb-backed IDs.
- Public Cinemeta fallback when AIOMetadata is not configured.
- AIOStreams source lookup and direct HTTP/HTTPS playback.
- Optional local playback forwarder for source compatibility edge cases.
- Movie, series, season, episode, search, resume, Trakt, and view-mode workflows.
- Per-movie/per-episode resume history with source fallback when old saved stream URLs no longer work.
- Optional Trakt authentication, scrobbling, Resume, Next Up, watchlist, watched, and history lists.

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
4. Select `AIOStreams -> repository.aiostreams-0.1.2.zip`.
5. Open `Install from repository -> AIOStreams Repository -> Video add-ons`.
6. Install `AIOStreams for Kodi`.

When the hosted repository is updated, Kodi can update the add-on through the normal add-on update flow.

Direct zip fallback:

[repository.aiostreams-0.1.2.zip](https://needforseed1.github.io/aios-kodi/repository.aiostreams-0.1.2.zip)

## Beta Repository

Beta builds are published separately so stable repository users do not receive preview updates automatically. Add the beta repository URL as a Kodi file source only on devices where you want opt-in preview releases:

```text
https://needforseed1.github.io/aios-kodi/beta/
```

Then install the beta repository add-on:

```text
repository.aiostreams.beta-0.1.2.zip
```

The beta repository uses the same video add-on ID, so beta versions replace stable versions on devices where the beta repository is installed.

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
  "aiometadata_url": "",
  "trakt_enabled": false,
  "trakt_scrobble": true,
  "trakt_client_id": "",
  "trakt_client_secret": "",
  "trakt_redirect_uri": "urn:ietf:wg:oauth:2.0:oob"
}
```

For best matching, use a metadata provider that returns IMDb IDs. Cinemeta works as the default fallback for standard movie and series IDs.

### AIOStreams URL Workaround

AIOStreams and AIOMetadata install URLs can be long and usually contain private path segments for the user's configured provider stack. Kodi's settings editor is not always a good place to paste or maintain those URLs, especially on TV keyboards and remote-driven installs.

For that reason, the add-on supports the profile `credentials.json` file shown above. It is a practical workaround for entering the AIOStreams URLs outside Kodi's settings UI while still keeping them in Kodi's add-on data directory. Kodi settings still take precedence, so leave the settings fields blank if you want the JSON file values to be used.

The add-on accepts these aliases for the AIOStreams manifest URL:

```text
aiostreams_url
aio_streams_url
aio_url
aiostreams
```

And these aliases for the AIOMetadata manifest URL:

```text
aiometadata_url
aio_metadata_url
metadata_url
aiometadata
```

Trakt can also be configured in the same file. Set `trakt_enabled` to `true`, leave the Kodi Trakt settings blank if you want the file to be used, then run `Settings -> Authenticate Trakt` once inside the add-on.

## Trakt

Trakt integration is optional. Create a Trakt API app at:

```text
https://trakt.tv/oauth/applications
```

Then enable Trakt in the add-on settings and enter the client ID and client secret, or add them to `credentials.json` using the keys shown above. To avoid pasting values in Kodi, set `trakt_enabled` to `true` and add `trakt_client_id` and `trakt_client_secret` in the file. Set `trakt_scrobble` to `false` if you want Trakt lists without playback scrobbling.

In the add-on, open `Settings -> Authenticate Trakt` and approve the displayed device code in a browser. The add-on stores Trakt OAuth tokens in:

```text
<Kodi userdata>/addon_data/plugin.video.aiostreams/trakt_tokens.json
```

When enabled and authenticated, playback is scrobbled to Trakt from Kodi's background service. The add-on also adds a Trakt folder with:

- Resume progress from Trakt playback state.
- Next Up, built from recent watched episode history and Trakt watched-progress data.
- Watchlist movies and shows.
- Watched movies and shows.
- Recent movie and episode history.

Trakt watched data is used to set Kodi playcount markers in catalog and episode lists. Trakt items are enriched with AIOMetadata/Cinemeta metadata and artwork when an IMDb-backed ID is available. Trakt Resume and Next Up entries resolve through AIOStreams sources when opened; they do not store or reuse saved stream URLs.

Items need IMDb-backed IDs for reliable matching.

## Playback Forwarder

By default, playback is handed directly to Kodi. This is the fastest path for most working HTTP/HTTPS sources and avoids relaying the stream through the add-on's Python service.

The optional `Use local playback forwarder` setting makes Kodi play from `127.0.0.1` while the add-on service fetches the real upstream stream. Enable it only when direct playback has source-specific problems, for example:

- Required request headers are not handled correctly by Kodi's built-in HTTP client.
- A host behaves badly with redirects, authenticated URLs, or Basic Auth in the URL.
- Seeking or resume fails because the host is picky about Range requests.
- Direct playback fails but the same source works through the add-on forwarder.

The forwarder can be slower for high-bitrate streams because video data passes through Python before reaching Kodi. Resume tracking and Trakt scrobbling do not require the forwarder.

## Resume

Local resume history is stored in:

```text
<Kodi userdata>/addon_data/plugin.video.aiostreams/resume.json
```

Resume entries are keyed by movie or episode where possible, not by source stream. Changing source should update the same movie/episode resume item instead of creating a duplicate. If an old saved stream URL fails or no playable HTTP stream is returned, the add-on falls back to Search/Sources for the same movie or episode and keeps the saved resume offset.

The local Resume screen has a context-menu action named `Remove from Resume` for clearing an item from local resume history. Kodi's built-in watched context menu does not update this add-on's resume file.

## Layout and View Settings

Kodi view modes are not portable. The same visual layout can have different numeric IDs in different skins, and some skins ignore direct `Container.SetViewMode(...)` calls unless the view is applied after the directory finishes loading.

The add-on's `Settings -> View Mode Setup` menu lets you configure separate view IDs for:

- Main add-on lists
- Catalog results
- Search results
- Season folders
- Episode lists
- Source results

The setup screen shows view IDs detected from the active skin, plus common fallback candidates. When you choose a view, the add-on saves it as a raw Kodi view ID in the relevant setting.

Internally the add-on uses a two-step workaround:

1. It exposes skin hint properties named `aios_forced_view` and `aios_forced_view_id` for skins that know how to read them.
2. It falls back to calling `Container.SetViewMode(...)` after a short configurable delay.

The delay is controlled by `View apply delay in milliseconds`. Increase it if your skin opens the correct folder but lands on the wrong layout; set the relevant view setting to `0` to disable forcing for that section.

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
repo/repository.aiostreams/repository.aiostreams-0.1.2.zip
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

To build the beta repository tree locally:

```bash
python3 tools/build_repo.py --clean --output-dir repo-beta --base-url https://example.com/kodi/beta/ --repository-manifest repository.aiostreams.beta/addon.xml
```

## Publish With GitHub Pages

The `repository` workflow builds and deploys the repository tree to GitHub Pages on pushes to `main` and from manual workflow runs. Stable root repository files are built from the `stable` branch. Beta repository files are built from `main` and published under `/beta/`.

The published stable repository zip is available at:

```text
https://needforseed1.github.io/aios-kodi/repository.aiostreams/repository.aiostreams-0.1.2.zip
```

The published beta repository zip is available at:

```text
https://needforseed1.github.io/aios-kodi/beta/repository.aiostreams.beta/repository.aiostreams.beta-0.1.2.zip
```

## Development

Run the local checks:

```bash
python3 -m compileall -q addon.py service.py resources/lib
python3 -m pytest -q
```

Build artifacts in `repo/` and `builds/` are ignored by git.
