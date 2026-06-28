# AIOStreams for Kodi

Kodi video plugin that bridges Stremio-compatible metadata and stream addons:

- AIOMetadata for frontpage catalogs, lists, search, posters, and metadata, or Cinemeta when AIOMetadata is not configured.
- AIOStreams for stream/source lookup and playback links.

## Configure

Open the addon settings and paste the full AIOStreams install URL. AIOMetadata is optional; if it is left blank, the addon uses public Cinemeta for catalogs, search, and metadata. URLs should usually end with `manifest.json`, for example:

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
  "aiostreams_url": "https://example.com/stremio/<uuid>/<config>/manifest.json",
  "aiometadata_url": ""
}
```

Kodi settings take precedence when non-empty. The credentials file is the fallback.

For best AIOStreams matching, use a metadata provider that returns IMDb IDs when possible. Cinemeta works as the default fallback for standard movie and series IDs.

## Current Scope

- Browse AIOMetadata catalogs, or Cinemeta catalogs when AIOMetadata is unset.
- Use metadata-provider search catalogs.
- Show title details and series season/episode folders.
- Fetch source choices from AIOStreams.
- Play direct HTTP/HTTPS stream URLs returned by AIOStreams.

Torrent infohash-only streams are listed as unsupported because Kodi cannot play them directly without another torrent playback layer.
