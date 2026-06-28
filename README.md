# AIOStreams for Kodi

Kodi video plugin that bridges two Stremio-compatible addons:

- AIOMetadata for frontpage catalogs, lists, search, posters, and metadata.
- AIOStreams for stream/source lookup and playback links.

## Configure

Open the addon settings and paste the full install URLs for both services, or edit the external credentials file in the addon profile. Each URL should usually end with `manifest.json`, for example:

```text
https://example.com/stremio/<uuid>/<config>/manifest.json
```

The addon strips `/manifest.json` internally and calls the related `catalog`, `meta`, and `stream` endpoints.

To avoid storing personal install URLs in source control, the addon also reads:

```text
<Kodi userdata>/addon_data/plugin.video.aiostreams/credentials.json
```

Use the in-addon `Settings -> Credentials file location` action to create/show the exact path. The file format is:

```json
{
  "aiometadata_url": "https://example.com/stremio/<uuid>/<config>/manifest.json",
  "aiostreams_url": "https://example.com/stremio/<uuid>/<config>/manifest.json"
}
```

Kodi settings take precedence when non-empty. The credentials file is the fallback.

For best AIOStreams matching, configure AIOMetadata to provide IMDb IDs when possible.

## Current Scope

- Browse AIOMetadata catalogs.
- Use AIOMetadata search catalogs.
- Show title details and series season/episode folders.
- Fetch source choices from AIOStreams.
- Play direct HTTP/HTTPS stream URLs returned by AIOStreams.

Torrent infohash-only streams are listed as unsupported because Kodi cannot play them directly without another torrent playback layer.
