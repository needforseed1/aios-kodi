import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon
import xbmcvfs


ADDON = xbmcaddon.Addon("plugin.video.aiostreams")
API_URL = "https://api.trakt.tv"
USER_AGENT = "AIOStreams/0.1 Kodi"
CREDENTIALS_FILE = "credentials.json"
TOKENS_FILE = "trakt_tokens.json"
IMDB_RE = re.compile(r"^tt\d+$")


class TraktError(RuntimeError):
    pass


class TraktAuthError(TraktError):
    pass


class TraktNotConfigured(TraktError):
    pass


def setting(setting_id):
    return (ADDON.getSetting(setting_id) or "").strip()


def setting_bool(setting_id, default=False):
    value = setting(setting_id).lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return default


def profile_path(filename):
    profile = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    if not os.path.isdir(profile):
        os.makedirs(profile, exist_ok=True)
    return os.path.join(profile, filename)


def atomic_json_dump(path, data, **kwargs):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, **kwargs)
    os.replace(tmp_path, path)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def load_credentials():
    return load_json(profile_path(CREDENTIALS_FILE), {})


def credential_value(*keys):
    data = load_credentials()
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def client_id():
    return setting("trakt_client_id") or credential_value("trakt_client_id", "trakt_api_key", "trakt_client")


def client_secret():
    return setting("trakt_client_secret") or credential_value("trakt_client_secret", "trakt_secret")


def redirect_uri():
    return setting("trakt_redirect_uri") or credential_value("trakt_redirect_uri") or "urn:ietf:wg:oauth:2.0:oob"


def configured():
    return bool(client_id() and client_secret())


def enabled():
    return setting_bool("trakt_enabled", False) and configured()


def scrobble_enabled():
    return enabled() and setting_bool("trakt_scrobble", True)


def load_tokens():
    data = load_json(profile_path(TOKENS_FILE), {})
    return data if isinstance(data, dict) else {}


def save_tokens(data):
    data = dict(data or {})
    if not data.get("created_at"):
        data["created_at"] = int(time.time())
    try:
        atomic_json_dump(profile_path(TOKENS_FILE), data, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams Trakt token save failed: %s" % exc, xbmc.LOGWARNING)


def clear_tokens():
    try:
        atomic_json_dump(profile_path(TOKENS_FILE), {}, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams Trakt token clear failed: %s" % exc, xbmc.LOGWARNING)


def authenticated():
    return bool(load_tokens().get("access_token"))


def token_expires_soon(tokens):
    created_at = safe_int(tokens.get("created_at"))
    expires_in = safe_int(tokens.get("expires_in"))
    if not created_at or not expires_in:
        return True
    return time.time() >= created_at + expires_in - 3600


def safe_int(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def ensure_configured():
    if not configured():
        raise TraktNotConfigured("Trakt client ID and secret are not configured")


def headers(access_token=""):
    ensure_configured()
    result = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "trakt-api-key": client_id(),
        "trakt-api-version": "2",
    }
    if access_token:
        result["Authorization"] = "Bearer " + access_token
    return result


def request_json(path, method="GET", body=None, access_token="", allowed_statuses=None):
    allowed_statuses = set(allowed_statuses or (200, 201, 204))
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        API_URL + path,
        data=data,
        method=method,
        headers=headers(access_token),
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            response_body = response.read().decode("utf-8")
            return response.status, json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", "replace")
        payload = {}
        if response_body:
            try:
                payload = json.loads(response_body)
            except ValueError:
                payload = {"error": response_body}
        if exc.code in allowed_statuses:
            return exc.code, payload
        message = payload.get("error_description") or payload.get("error") or payload.get("message") or "HTTP %s" % exc.code
        if exc.code == 401:
            raise TraktAuthError(message)
        raise TraktError(message)
    except urllib.error.URLError as exc:
        raise TraktError("Network error: %s" % exc.reason)
    except (ValueError, UnicodeDecodeError) as exc:
        raise TraktError("Invalid JSON from Trakt: %s" % exc)


def device_code():
    _, data = request_json("/oauth/device/code", method="POST", body={"client_id": client_id()})
    return data


def poll_device_token(code):
    body = {
        "code": code,
        "client_id": client_id(),
        "client_secret": client_secret(),
    }
    return request_json("/oauth/device/token", method="POST", body=body, allowed_statuses=(200, 400, 404, 409, 410, 418, 429))


def refresh_access_token(tokens=None):
    ensure_configured()
    tokens = tokens or load_tokens()
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise TraktAuthError("Trakt refresh token is missing")
    body = {
        "refresh_token": refresh_token,
        "client_id": client_id(),
        "client_secret": client_secret(),
        "redirect_uri": redirect_uri(),
        "grant_type": "refresh_token",
    }
    _, data = request_json("/oauth/token", method="POST", body=body)
    save_tokens(data)
    return data.get("access_token") or ""


def valid_access_token():
    ensure_configured()
    tokens = load_tokens()
    access_token = tokens.get("access_token")
    if not access_token:
        raise TraktAuthError("Trakt is not authenticated")
    if token_expires_soon(tokens):
        return refresh_access_token(tokens)
    return access_token


def api_request(path, method="GET", body=None, oauth=True):
    access_token = valid_access_token() if oauth else ""
    try:
        _, data = request_json(path, method=method, body=body, access_token=access_token)
        return data
    except TraktAuthError:
        if not oauth:
            raise
        access_token = refresh_access_token()
        _, data = request_json(path, method=method, body=body, access_token=access_token)
        return data


def with_query(path, params):
    params = {
        key: value for key, value in (params or {}).items()
        if value not in (None, "")
    }
    if not params:
        return path
    return path + "?" + urllib.parse.urlencode(params)


def playback(media_type, limit=50):
    return api_request(with_query("/sync/playback/%s" % media_type, {
        "extended": "full",
        "limit": limit,
    }))


def watchlist(media_type, limit=50):
    return api_request(with_query("/sync/watchlist/%s/rank/asc" % media_type, {
        "extended": "full",
        "limit": limit,
    }))


def history(media_type, limit=50):
    return api_request(with_query("/sync/history/%s" % media_type, {
        "extended": "full",
        "limit": limit,
    }))


def watched(media_type):
    return api_request(with_query("/sync/watched/%s" % media_type, {
        "extended": "full",
    }))


def revoke():
    tokens = load_tokens()
    token = tokens.get("access_token")
    if token and configured():
        try:
            request_json("/oauth/revoke", method="POST", body={
                "token": token,
                "client_id": client_id(),
                "client_secret": client_secret(),
            }, allowed_statuses=(200, 204, 400, 401))
        except TraktError as exc:
            xbmc.log("AIOStreams Trakt revoke failed: %s" % exc, xbmc.LOGWARNING)
    clear_tokens()


def imdb_id(value):
    text = str(value or "").split(":")[0]
    return text if IMDB_RE.match(text) else ""


def parse_episode_id(value):
    parts = str(value or "").split(":")
    if len(parts) >= 3 and parts[-2].isdigit() and parts[-1].isdigit():
        return imdb_id(parts[0]), parts[-2], parts[-1]
    return "", "", ""


def media_from_context(context):
    context = context or {}
    item_type = context.get("item_type") or ""
    item_id = context.get("item_id") or ""
    if item_type == "movie":
        movie_imdb = imdb_id(context.get("imdb_id") or item_id)
        return {"movie": {"ids": {"imdb": movie_imdb}}} if movie_imdb else {}

    if item_type not in ("series", "episode"):
        return {}

    parsed_show_imdb, parsed_season, parsed_episode = parse_episode_id(context.get("video_id") or item_id)
    show_imdb = imdb_id(context.get("show_imdb") or context.get("show_id") or parsed_show_imdb)
    season = str(context.get("season") or parsed_season or "")
    episode = str(context.get("episode") or parsed_episode or "")
    episode_imdb_source = context.get("episode_imdb")
    if not episode_imdb_source and season.isdigit() and episode.isdigit() and ":" not in str(item_id):
        episode_imdb_source = item_id
    episode_imdb = imdb_id(episode_imdb_source)

    episode_obj = {}
    if season.isdigit() and episode.isdigit():
        episode_obj["season"] = int(season)
        episode_obj["number"] = int(episode)
    if episode_imdb and episode_imdb != show_imdb:
        episode_obj["ids"] = {"imdb": episode_imdb}
    if not episode_obj:
        return {}

    media = {"episode": episode_obj}
    if show_imdb:
        media["show"] = {"ids": {"imdb": show_imdb}}
    return media


def progress_percent(position, duration):
    position = safe_int(position)
    duration = safe_int(duration)
    if position <= 0 or duration <= 0:
        return 0.0
    return max(0.0, min(100.0, round(position * 100.0 / duration, 2)))


def scrobble_payload(context, position, duration):
    media = media_from_context(context)
    if not media:
        return {}
    payload = dict(media)
    payload["progress"] = progress_percent(position, duration)
    return payload


def scrobble(action, context, position, duration):
    if not scrobble_enabled():
        return False
    payload = scrobble_payload(context, position, duration)
    if not payload:
        return False
    if action == "stop" and payload.get("progress", 0) < 1:
        return False
    try:
        api_request("/scrobble/%s" % action, method="POST", body=payload)
        return True
    except TraktError as exc:
        xbmc.log("AIOStreams Trakt scrobble %s failed: %s" % (action, exc), xbmc.LOGWARNING)
        return False
