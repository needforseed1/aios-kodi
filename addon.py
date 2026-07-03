import base64
import concurrent.futures
import html
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib import trakt, views


ADDON = xbmcaddon.Addon()
HANDLE = int(sys.argv[1])
PLUGIN_URL = sys.argv[0]
USER_AGENT = "AIOStreams/0.1 Kodi"
DEFAULT_AIOMETADATA_URL = ""
DEFAULT_AIOSTREAMS_URL = ""
CINEMETA_URL = "https://v3-cinemeta.strem.io"
VIEW_SECTIONS = (
    ("view_root", "Main Addon Lists", "videos"),
    ("view_results", "Catalog Results", "movies"),
    ("view_search", "Search Results", "movies"),
    ("view_seasons", "Season Folders", "seasons"),
    ("view_episodes", "Episode Lists", "episodes"),
    ("view_sources", "Source Results", "videos"),
)
AIOS_FORCED_VIEW_PROPERTY = "aios_forced_view"
AIOS_FORCED_VIEW_ID_PROPERTY = "aios_forced_view_id"
VIEW_CANDIDATES = (
    (50, "50 - List / default common"),
    (51, "51 - Poster / thumbnails common"),
    (52, "52 - Icon wall common"),
    (53, "53 - Shift common"),
    (54, "54 - Info wall common"),
    (55, "55 - Wide list common"),
    (56, "56 - Wall common"),
    (500, "500 - Arctic candidate"),
    (501, "501 - Arctic candidate"),
    (502, "502 - Arctic candidate"),
    (503, "503 - Arctic candidate"),
    (504, "504 - Arctic candidate"),
    (505, "505 - Arctic candidate"),
    (510, "510 - Arctic candidate"),
    (511, "511 - Arctic candidate"),
    (512, "512 - Arctic candidate"),
    (515, "515 - Arctic candidate"),
    (520, "520 - Arctic candidate"),
    (550, "550 - Arctic candidate"),
    (551, "551 - Arctic candidate"),
    (552, "552 - Arctic candidate"),
)
RESUME_FILE = "resume.json"
CURRENT_PLAYBACK_FILE = "current_playback.json"
SEARCH_CACHE_FILE = "search_cache.json"
API_CACHE_FILE = "api_cache.json"
IMDB_EPISODE_SCORES_FILE = "imdb_episode_scores.json"
FORWARDER_TOKENS_FILE = "forwarder_tokens.json"
STREAM_CONTEXTS_FILE = "stream_contexts.json"
CREDENTIALS_FILE = "credentials.json"
AIOS_CREDENTIAL_KEYS = ("aiostreams_url", "aio_streams_url", "aio_url", "aiostreams")
AIOMETA_CREDENTIAL_KEYS = ("aiometadata_url", "aio_metadata_url", "metadata_url", "aiometadata")
IMDB_GRAPHQL_URL = "https://api.graphql.imdb.com/"
IMDB_SCORE_REFRESH_RECENT = 24 * 3600
IMDB_SCORE_REFRESH_CURRENT = 3 * 24 * 3600
IMDB_SCORE_REFRESH_OLD = 30 * 24 * 3600
SEARCH_CACHE_TTL = 30 * 60
META_CACHE_TTL = 30 * 60
STREAM_CONTEXT_TTL = 6 * 3600
STREAM_CONTEXT_LIMIT = 300
PLAYBACK_HEADER_DENYLIST = {"range", "if-range", "content-range", "content-length"}
TRAKT_WATCHED_TTL = 60
TRAKT_NEXT_SHOW_LIMIT = 15

views.init(ADDON, HANDLE, VIEW_SECTIONS, VIEW_CANDIDATES)


def addon_url(**params):
    return PLUGIN_URL + "?" + urllib.parse.urlencode(params)


def notify(message):
    xbmcgui.Dialog().notification("AIOStreams", message, xbmcgui.NOTIFICATION_INFO, 5000)


def error(message):
    xbmcgui.Dialog().notification("AIOStreams", message, xbmcgui.NOTIFICATION_ERROR, 7000)


def setting(setting_id):
    return (ADDON.getSetting(setting_id) or "").strip()


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


def credentials_path():
    return profile_path(CREDENTIALS_FILE)


def load_credentials():
    path = credentials_path()
    if not os.path.exists(path):
        save_credentials_template(path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_credentials_status():
    path = credentials_path()
    if not os.path.exists(path):
        save_credentials_template(path)
        return {}, "created empty template"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        return {}, "read error: %s" % exc
    except ValueError as exc:
        return {}, "invalid JSON: %s" % exc
    if not isinstance(data, dict):
        return {}, "invalid JSON shape: expected object"
    return data, "loaded"


def credential_value_from(data, keys):
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def credential_bool_from(data, key, default=False):
    if not isinstance(data, dict) or key not in data:
        return default
    value = data.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
    return default


def credential_value(*keys):
    return credential_value_from(load_credentials(), keys)


def save_credentials_template(path):
    data = {
        "aiostreams_url": "",
        "aiometadata_url": "",
        "trakt_enabled": False,
        "trakt_scrobble": True,
        "trakt_client_id": "",
        "trakt_client_secret": "",
        "trakt_redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
    }
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except OSError as exc:
        xbmc.log("AIOStreams could not create credentials file: %s" % exc, xbmc.LOGWARNING)


def load_resume_entries():
    path = profile_path(RESUME_FILE)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else []
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def save_resume_entries(entries):
    path = profile_path(RESUME_FILE)
    try:
        atomic_json_dump(path, {"entries": entries}, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams could not save resume history: %s" % exc, xbmc.LOGWARNING)


def remove_resume_entry(key):
    if not key:
        return False
    entries = load_resume_entries()
    kept = [entry for entry in entries if entry.get("key") != key]
    if len(kept) == len(entries):
        return False
    save_resume_entries(kept)
    return True


def load_search_cache():
    path = profile_path(SEARCH_CACHE_FILE)
    if not os.path.exists(path):
        return {"entries": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"entries": {}}
    return data


def save_search_cache(data):
    entries = data.get("entries") if isinstance(data, dict) else {}
    if not isinstance(entries, dict):
        entries = {}
    sorted_entries = sorted(entries.items(), key=lambda item: item[1].get("updated", 0), reverse=True)[:20]
    path = profile_path(SEARCH_CACHE_FILE)
    try:
        atomic_json_dump(path, {"entries": dict(sorted_entries)}, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams could not save search cache: %s" % exc, xbmc.LOGWARNING)


def clear_search_cache():
    save_search_cache({"entries": {}})


def load_api_cache():
    path = profile_path(API_CACHE_FILE)
    if not os.path.exists(path):
        return {"entries": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"entries": {}}
    return data


def save_api_cache(data):
    entries = data.get("entries") if isinstance(data, dict) else {}
    if not isinstance(entries, dict):
        entries = {}
    now = int(time.time())
    kept = {}
    for key, entry in sorted(entries.items(), key=lambda item: item[1].get("updated", 0), reverse=True)[:100]:
        ttl = safe_int(entry.get("ttl"))
        if ttl and now - safe_int(entry.get("updated")) > ttl:
            continue
        kept[key] = entry
    path = profile_path(API_CACHE_FILE)
    try:
        atomic_json_dump(path, {"entries": kept}, separators=(",", ":"))
    except OSError as exc:
        xbmc.log("AIOStreams could not save API cache: %s" % exc, xbmc.LOGWARNING)


_API_CACHE = None
_API_CACHE_DIRTY = False
_API_CACHE_LOCK = threading.Lock()


def api_cache_data():
    global _API_CACHE
    with _API_CACHE_LOCK:
        if _API_CACHE is None:
            _API_CACHE = load_api_cache()
        return _API_CACHE


def flush_api_cache():
    global _API_CACHE_DIRTY
    with _API_CACHE_LOCK:
        if not _API_CACHE_DIRTY or _API_CACHE is None:
            return
        data = _API_CACHE
        _API_CACHE_DIRTY = False
    save_api_cache(data)


def clear_api_cache():
    global _API_CACHE, _API_CACHE_DIRTY
    with _API_CACHE_LOCK:
        _API_CACHE = {"entries": {}}
        _API_CACHE_DIRTY = False
    save_api_cache({"entries": {}})


def cached_service_json(url, service, ttl):
    global _API_CACHE_DIRTY
    final_url = service_url(url, service)
    key = hashlib.sha1(final_url.encode("utf-8")).hexdigest()
    cache = api_cache_data()
    with _API_CACHE_LOCK:
        entry = cache.get("entries", {}).get(key, {})
        if entry and int(time.time()) - safe_int(entry.get("updated")) <= ttl and isinstance(entry.get("data"), dict):
            return entry.get("data")
    data = get_json(final_url)
    with _API_CACHE_LOCK:
        cache.setdefault("entries", {})[key] = {"updated": int(time.time()), "ttl": ttl, "data": data}
        _API_CACHE_DIRTY = True
    return data


def parallel_map(func, items, max_workers=8):
    items = list(items)
    if len(items) <= 1:
        return [func(item) for item in items]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        return list(pool.map(func, items))


def search_cache_key(search_type, query):
    value = "%s|%s|%s" % (aiometa_base(), search_type or "", query.strip().lower())
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def cached_search_results(search_type, query):
    entry = load_search_cache().get("entries", {}).get(search_cache_key(search_type, query), {})
    if not entry or int(time.time()) - safe_int(entry.get("updated")) > SEARCH_CACHE_TTL:
        return None
    results = entry.get("results")
    if not isinstance(results, list):
        return None
    valid_results = []
    for result in results:
        if not isinstance(result, dict):
            continue
        if isinstance(result.get("meta"), dict) and result.get("type") and result.get("id"):
            valid_results.append(result)
    return valid_results or None


def save_search_results(search_type, query, results):
    data = load_search_cache()
    data.setdefault("entries", {})[search_cache_key(search_type, query)] = {
        "query": query,
        "search_type": search_type,
        "updated": int(time.time()),
        "results": results,
    }
    save_search_cache(data)


def save_current_playback(context):
    path = profile_path(CURRENT_PLAYBACK_FILE)
    context["started"] = int(time.time())
    try:
        atomic_json_dump(path, context, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams could not save current playback context: %s" % exc, xbmc.LOGWARNING)


def load_stream_contexts():
    path = profile_path(STREAM_CONTEXTS_FILE)
    if not os.path.exists(path):
        return {"contexts": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"contexts": {}}
    if not isinstance(data, dict) or not isinstance(data.get("contexts"), dict):
        return {"contexts": {}}
    return data


def save_stream_contexts(data):
    now = int(time.time())
    contexts = data.get("contexts") if isinstance(data, dict) else {}
    if not isinstance(contexts, dict):
        contexts = {}
    contexts = {
        key: value for key, value in contexts.items()
        if isinstance(value, dict) and now - safe_int(value.get("updated")) < STREAM_CONTEXT_TTL
    }
    if len(contexts) > STREAM_CONTEXT_LIMIT:
        newest = sorted(contexts.items(), key=lambda item: safe_int(item[1].get("updated")), reverse=True)
        contexts = dict(newest[:STREAM_CONTEXT_LIMIT])
    path = profile_path(STREAM_CONTEXTS_FILE)
    try:
        atomic_json_dump(path, {"contexts": contexts}, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams could not save stream context: %s" % exc, xbmc.LOGWARNING)


def stream_context_token():
    return base64.urlsafe_b64encode(os.urandom(18)).decode("ascii").rstrip("=")


def register_stream_context(context, data=None, save=True):
    data = data or load_stream_contexts()
    contexts = data.setdefault("contexts", {})
    token = stream_context_token()
    stored = dict(context)
    stored["headers"] = sanitize_playback_headers(stored.get("headers") or {})
    stored["updated"] = int(time.time())
    contexts[token] = stored
    if save:
        save_stream_contexts(data)
    return token


def load_stream_context(token):
    if not token:
        return {}
    entry = load_stream_contexts().get("contexts", {}).get(token)
    if not isinstance(entry, dict):
        return {}
    if int(time.time()) - safe_int(entry.get("updated")) > STREAM_CONTEXT_TTL:
        return {}
    return entry


def load_forwarder_tokens():
    path = profile_path(FORWARDER_TOKENS_FILE)
    if not os.path.exists(path):
        return {"tokens": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"tokens": {}}
    if not isinstance(data, dict) or not isinstance(data.get("tokens"), dict):
        return {"tokens": {}}
    return data


def save_forwarder_tokens(data):
    now = int(time.time())
    tokens = data.get("tokens") if isinstance(data, dict) else {}
    if not isinstance(tokens, dict):
        tokens = {}
    tokens = {
        key: value for key, value in tokens.items()
        if isinstance(value, dict) and now - safe_int(value.get("updated")) < 6 * 3600
    }
    path = profile_path(FORWARDER_TOKENS_FILE)
    try:
        atomic_json_dump(path, {"tokens": tokens}, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams could not save forwarder token: %s" % exc, xbmc.LOGWARNING)


def safe_filename(value, fallback="stream.mkv"):
    text = urllib.parse.unquote(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|]+", ".", text)
    text = re.sub(r"\s+", ".", text).strip(".")
    if not text:
        text = fallback
    if not os.path.splitext(text)[1]:
        text += ".mkv"
    return text[:180]


def forwarder_available(port):
    deadline = time.time() + 1.5
    while time.time() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            sock.connect(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.1)
        finally:
            try:
                sock.close()
            except OSError:
                pass
    return False


def register_forwarder_url(playback_url, playback_headers, context):
    data = load_forwarder_tokens()
    tokens = data.setdefault("tokens", {})
    token = hashlib.sha1(("%s|%s|%s" % (time.time(), playback_url, os.urandom(12))).encode("utf-8")).hexdigest()
    parsed = urllib.parse.urlparse(playback_url)
    filename = safe_filename(context.get("stream_title") or os.path.basename(parsed.path) or context.get("title"))
    tokens[token] = {
        "url": playback_url,
        "headers": playback_headers,
        "filename": filename,
        "updated": int(time.time()),
    }
    save_forwarder_tokens(data)
    port = setting_int("forwarder_port", 45987)
    if not forwarder_available(port):
        raise RuntimeError("local forwarder is not listening on 127.0.0.1:%s" % port)
    return "http://127.0.0.1:%d/play/%s/%s" % (port, urllib.parse.quote(token, safe=""), urllib.parse.quote(filename))


def endpoint_base(setting_id):
    return endpoint_base_value(setting(setting_id))


def credential_endpoint_base(key):
    if key == "aiostreams_url":
        return endpoint_base_value(credential_value(*AIOS_CREDENTIAL_KEYS))
    if key == "aiometadata_url":
        return endpoint_base_value(credential_value(*AIOMETA_CREDENTIAL_KEYS))
    return endpoint_base_value(credential_value(key))


def endpoint_base_value(value):
    value = str(value or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    path = parsed.path.rstrip("/")
    if path.endswith("/manifest.json"):
        path = path[: -len("/manifest.json")]
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")
    return value


def join_url(base, *parts):
    path = "/".join(urllib.parse.quote(str(part), safe="") for part in parts if part != "")
    return base.rstrip("/") + "/" + path


def redact_url(url):
    parsed = urllib.parse.urlparse(str(url or ""))
    if not parsed.scheme or not parsed.netloc:
        return str(url or "")
    return "%s://%s/..." % (parsed.scheme, parsed.netloc)


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=setting_int("request_timeout", 25)) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("HTTP %s from %s" % (exc.code, redact_url(url)))
    except urllib.error.URLError as exc:
        raise RuntimeError("Network error: %s" % exc.reason)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Invalid JSON from %s: %s" % (redact_url(url), exc))


def with_query_param(url, key, value):
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return "%s%s%s=%s" % (url, separator, urllib.parse.quote(key), urllib.parse.quote(str(value)))


def refresh_setting(service):
    return "%s_refresh_token" % service


def service_url(url, service):
    token = setting(refresh_setting(service))
    if not token:
        return url
    return with_query_param(url, "_kodi_refresh", token)


def imdb_episode_scores_enabled():
    return setting_bool("imdb_episode_scores", True)


def setting_bool(setting_id, default=False):
    value = setting(setting_id).lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return default


def load_imdb_episode_scores():
    path = profile_path(IMDB_EPISODE_SCORES_FILE)
    if not os.path.exists(path):
        return {"shows": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"shows": {}}
    if not isinstance(data, dict):
        return {"shows": {}}
    if not isinstance(data.get("shows"), dict):
        data["shows"] = {}
    return data


def save_imdb_episode_scores(data):
    path = profile_path(IMDB_EPISODE_SCORES_FILE)
    try:
        atomic_json_dump(path, data, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams could not save IMDb episode scores: %s" % exc, xbmc.LOGWARNING)


def imdb_show_cache(data, imdb_id):
    shows = data.setdefault("shows", {})
    show = shows.setdefault(imdb_id, {"seasons": {}, "updated": 0})
    if not isinstance(show.get("seasons"), dict):
        show["seasons"] = {}
    return show


def imdb_cached_episode_score(data, imdb_id, season, episode):
    show = data.get("shows", {}).get(imdb_id, {})
    season_data = show.get("seasons", {}).get(str(season), {})
    entry = season_data.get("episodes", {}).get(str(episode), {})
    return entry if isinstance(entry, dict) and entry.get("rating") else None


def imdb_cache_stale(data, imdb_id, season, videos):
    show = data.get("shows", {}).get(imdb_id, {})
    season_data = show.get("seasons", {}).get(str(season), {})
    updated = safe_int(season_data.get("updated"))
    if not updated:
        return True
    return int(time.time()) - updated > imdb_refresh_interval(videos)


def imdb_refresh_interval(videos):
    newest_age = newest_video_age_days(videos)
    if newest_age is not None and newest_age <= 7:
        return IMDB_SCORE_REFRESH_RECENT
    if newest_age is not None and newest_age <= 30:
        return IMDB_SCORE_REFRESH_CURRENT
    return IMDB_SCORE_REFRESH_OLD


def newest_video_age_days(videos):
    newest = None
    for video in videos:
        value = video.get("released") or video.get("firstAired") or video.get("airDate")
        if not value:
            continue
        try:
            timestamp = time.mktime(time.strptime(str(value)[:10], "%Y-%m-%d"))
        except (TypeError, ValueError):
            continue
        if newest is None or timestamp > newest:
            newest = timestamp
    if newest is None:
        return None
    return max(0, int((time.time() - newest) / 86400))


def imdb_graphql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(IMDB_GRAPHQL_URL, data=body, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=setting_int("request_timeout", 25)) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("IMDb HTTP %s" % exc.code)
    except urllib.error.URLError as exc:
        raise RuntimeError("IMDb network error: %s" % exc.reason)
    except ValueError:
        raise RuntimeError("IMDb returned invalid JSON")
    if data.get("errors"):
        raise RuntimeError((data.get("errors") or [{}])[0].get("message") or "IMDb GraphQL error")
    return data.get("data") or {}


def fetch_imdb_season_scores(imdb_id, season):
    query = """
    query($id: ID!, $season: String!, $after: ID) {
      title(id: $id) {
        episodes {
          episodes(first: 100, after: $after, filter: {includeSeasons: [$season]}, sort: {by: EPISODE_THEN_RELEASE, order: ASC}) {
            pageInfo { hasNextPage endCursor }
            edges {
              node {
                id
                series { displayableEpisodeNumber { displayableSeason { season } episodeNumber { episodeNumber } } }
                ratingsSummary { aggregateRating voteCount }
              }
            }
          }
        }
      }
    }
    """
    scores = {}
    after = None
    while True:
        data = imdb_graphql(query, {"id": imdb_id, "season": str(season), "after": after})
        connection = (((data.get("title") or {}).get("episodes") or {}).get("episodes") or {})
        for edge in connection.get("edges") or []:
            node = edge.get("node") or {}
            display = (((node.get("series") or {}).get("displayableEpisodeNumber") or {}))
            display_season = ((display.get("displayableSeason") or {}).get("season"))
            display_episode = ((display.get("episodeNumber") or {}).get("episodeNumber"))
            rating = ((node.get("ratingsSummary") or {}).get("aggregateRating"))
            if str(display_season) == str(season) and display_episode and rating:
                scores[str(display_episode)] = {
                    "episode_id": node.get("id") or "",
                    "rating": float(rating),
                    "votes": safe_int((node.get("ratingsSummary") or {}).get("voteCount")),
                }
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            break
        after = page_info.get("endCursor")
    return scores


def refresh_imdb_season_scores(imdb_id, season):
    if not imdb_episode_scores_enabled() or not imdb_id or not str(imdb_id).startswith("tt"):
        return False
    try:
        scores = fetch_imdb_season_scores(imdb_id, season)
    except RuntimeError as exc:
        xbmc.log("AIOStreams IMDb episode score fetch failed for %s season %s: %s" % (imdb_id, season, exc), xbmc.LOGWARNING)
        return False
    data = load_imdb_episode_scores()
    show = imdb_show_cache(data, imdb_id)
    show["seasons"][str(season)] = {"updated": int(time.time()), "episodes": scores}
    show["updated"] = int(time.time())
    save_imdb_episode_scores(data)
    xbmc.log("AIOStreams cached %d IMDb episode scores for %s season %s" % (len(scores), imdb_id, season), xbmc.LOGINFO)
    return bool(scores)


def queue_imdb_show_scores(item_type, item_id):
    if not imdb_episode_scores_enabled() or item_type != "series":
        return
    try:
        xbmc.executebuiltin("RunPlugin(%s)" % addon_url(action="imdb_refresh_show", type=item_type, id=item_id, quiet="1"))
    except Exception as exc:
        xbmc.log("AIOStreams could not queue IMDb episode score refresh: %s" % exc, xbmc.LOGWARNING)


def queue_imdb_season_scores(item_type, item_id, season):
    try:
        xbmc.executebuiltin("RunPlugin(%s)" % addon_url(action="imdb_refresh_season", type=item_type, id=item_id, season=season))
    except Exception as exc:
        xbmc.log("AIOStreams could not queue IMDb season score refresh: %s" % exc, xbmc.LOGWARNING)


def refresh_imdb_season_worker(item_type, item_id, season):
    if not imdb_episode_scores_enabled() or not item_id:
        return
    url = join_url(aiometa_base(), "meta", item_type, item_id) + ".json"
    try:
        response = cached_service_json(url, "aiometadata", META_CACHE_TTL)
    except RuntimeError as exc:
        xbmc.log("AIOStreams IMDb season refresh could not load metadata for %s: %s" % (item_id, exc), xbmc.LOGWARNING)
        return
    meta = response.get("meta") or {}
    imdb_id = tvmaze_imdb_id(meta, item_id)
    if imdb_id:
        refresh_imdb_season_scores(imdb_id, season)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_playback_redirect(url, headers=None):
    request_headers = sanitize_playback_headers(headers)
    if not any(str(key).lower() == "user-agent" for key in request_headers):
        request_headers["User-Agent"] = USER_AGENT
    opener = urllib.request.build_opener(NoRedirectHandler)
    timeout = setting_int("request_timeout", 25)
    # Probe with HEAD first so upstream never starts streaming (or burning a
    # one-shot debrid link); fall back to a 1-byte ranged GET for servers that
    # reject HEAD outright.
    for method, byte_range in (("HEAD", ""), ("GET", "bytes=0-0")):
        attempt_headers = dict(request_headers)
        if byte_range:
            attempt_headers["Range"] = byte_range
        request = urllib.request.Request(url, headers=attempt_headers, method=method)
        try:
            opener.open(request, timeout=timeout).close()
            return url
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location")
                if location:
                    return urllib.parse.urljoin(url, location)
            if method == "HEAD":
                continue
            raise RuntimeError("HTTP %s from %s" % (exc.code, redact_url(url)))
        except urllib.error.URLError as exc:
            raise RuntimeError("Network error: %s" % exc.reason)
    return url


def setting_int(setting_id, default):
    try:
        return int(setting(setting_id) or default)
    except ValueError:
        return default


def aiometa_base():
    return configured_aiometa_base() or CINEMETA_URL


def configured_aiometa_base():
    return endpoint_base("aiometadata_url") or credential_endpoint_base("aiometadata_url") or endpoint_base_value(DEFAULT_AIOMETADATA_URL)


def metadata_provider_label():
    return "AIOMetadata" if configured_aiometa_base() else "Cinemeta"


def aiostreams_base():
    return endpoint_base("aiostreams_url") or credential_endpoint_base("aiostreams_url") or endpoint_base_value(DEFAULT_AIOSTREAMS_URL)


def get_manifest():
    base = aiometa_base()
    return cached_service_json(base + "/manifest.json", "aiometadata", META_CACHE_TTL)


def find_catalog(catalog_type, catalog_id):
    for catalog in get_manifest().get("catalogs", []):
        if catalog.get("type") == catalog_type and catalog.get("id") == catalog_id:
            return catalog
    return {}


def apply_context_menu(item, context_menu=None, replace_context=False):
    if not context_menu:
        return
    try:
        item.addContextMenuItems(context_menu, replaceItems=replace_context)
    except AttributeError:
        pass


def add_directory(label, params, art=None, info=None, context_menu=None, replace_context=False):
    item = xbmcgui.ListItem(label=label)
    set_item_art(item, art)
    if info:
        set_item_info(item, info)
    apply_context_menu(item, context_menu, replace_context)
    xbmcplugin.addDirectoryItem(HANDLE, addon_url(**params), item, True)


def add_action(label, params, art=None, info=None, context_menu=None, replace_context=False):
    item = xbmcgui.ListItem(label=label)
    set_item_art(item, art)
    if info:
        set_item_info(item, info)
    apply_context_menu(item, context_menu, replace_context)
    xbmcplugin.addDirectoryItem(HANDLE, addon_url(**params), item, False)


def add_playable(label, params, art=None, info=None, context_menu=None, replace_context=False):
    item = xbmcgui.ListItem(label=label)
    item.setProperty("IsPlayable", "true")
    set_item_art(item, art)
    if info:
        set_item_info(item, info)
    apply_context_menu(item, context_menu, replace_context)
    xbmcplugin.addDirectoryItem(HANDLE, addon_url(**params), item, False)


def set_item_info(item, info):
    rating = display_rating(info.get("imdbRating") or info.get("rating"))
    votes = safe_int(info.get("imdbVotes") or info.get("votes"))
    tag = video_info_tag(item)
    if tag is not None:
        apply_info_tag(tag, info, rating, votes)
        return
    # Kodi 19 fallback: InfoTagVideo has no setters there.
    video_info = dict(info)
    video_info.pop("imdbVotes", None)
    item.setInfo("video", video_info)
    if not rating:
        return
    try:
        item.setRating("imdb", float(rating), votes, True)
    except (AttributeError, TypeError, ValueError):
        pass


def video_info_tag(item):
    try:
        tag = item.getVideoInfoTag()
    except (AttributeError, RuntimeError):
        return None
    return tag if hasattr(tag, "setTitle") else None


def apply_info_tag(tag, info, rating, votes):
    for key, setter_name, convert in (
        ("title", "setTitle", str),
        ("plot", "setPlot", str),
        ("plotoutline", "setPlotOutline", str),
        ("tvshowtitle", "setTvShowTitle", str),
        ("mediatype", "setMediaType", str),
        ("aired", "setFirstAired", str),
        ("premiered", "setPremiered", str),
        ("duration", "setDuration", int),
        ("year", "setYear", int),
        ("season", "setSeason", int),
        ("episode", "setEpisode", int),
        ("playcount", "setPlaycount", int),
    ):
        value = info.get(key)
        if value in (None, ""):
            continue
        try:
            getattr(tag, setter_name)(convert(value))
        except (AttributeError, TypeError, ValueError):
            pass
    genres = info.get("genre")
    if genres:
        try:
            tag.setGenres([str(genre) for genre in genres] if isinstance(genres, list) else [str(genres)])
        except (AttributeError, TypeError, ValueError):
            pass
    if rating:
        try:
            tag.setRating(float(rating), votes, "imdb", True)
        except (AttributeError, TypeError, ValueError):
            pass


def set_item_art(item, art):
    if not art:
        return
    item.setArt(art)
    if art.get("fanart"):
        item.setProperty("fanart_image", art.get("fanart"))


def art_json(art):
    if not art:
        return ""
    return json.dumps(art, separators=(",", ":"))


def art_from_json(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def add_video_item(meta, item_type, item_id, art=None, watched_ids=None, resume=0, duration=0, season="", episode="", resume_key=""):
    label = meta.get("name") or meta.get("title") or item_id
    item_art = art or meta_art(meta) or fallback_art(item_type_art_key(item_type))
    info = meta_info(meta)
    if watched_ids and best_stream_id(meta, item_id) in watched_ids:
        info["playcount"] = 1
    params = {"action": "details", "type": item_type, "id": item_id}
    if item_type == "movie":
        params = {"action": "streams", "type": item_type, "id": best_stream_id(meta, item_id), "title": label, "art": art_json(item_art)}
    if resume:
        params["resume"] = str(safe_int(resume))
    if duration:
        params["duration"] = str(safe_int(duration))
    if season:
        params["season"] = str(season)
    if episode:
        params["episode"] = str(episode)
    if resume_key:
        params["resume_key"] = str(resume_key)
    add_directory(label, params, item_art, info)


set_view = views.set_view
set_view_id = views.set_view_id


def meta_art(meta):
    poster_value = first_image(meta, (
        "poster",
        "posterUrl",
        "_rawPosterUrl",
        "thumbnail",
        "image",
        "still",
        "still_path",
        "screenshot",
    ))
    poster = image_url(poster_value)
    background = image_url(first_image(meta, (
        "background",
        "fanart",
        "backdrop",
        "backdropUrl",
        "_rawBackgroundUrl",
        "landscapePoster",
        "_rawLandscapePosterUrl",
    )))
    logo = image_url(first_image(meta, ("_rawLogoUrl", "logo", "clearLogo", "clearlogo")))
    art = {}
    if poster:
        art.update({"thumb": poster, "thumbnail": poster, "poster": poster, "icon": poster})
    if background:
        art["fanart"] = background
    if logo:
        art["clearlogo"] = logo
    return art


def fallback_art(key="default"):
    filename = {
        "movie": "movies_v2.png",
        "movies": "movies_v2.png",
        "series": "series_v2.png",
        "anime": "anime_v2.png",
        "anime.movie": "anime_v2.png",
        "anime.series": "anime_v2.png",
        "popular_movies": "popular_movies_v2.png",
        "popular_series": "popular_series_v2.png",
        "trending_movies": "trending_movies_v2.png",
        "trending_series": "trending_series_v2.png",
        "top_movies": "top_movies_v2.png",
        "top_series": "top_series_v2.png",
        "search": "search_v2.png",
        "settings": "settings_v2.png",
        "sources": "sources_v2.png",
        "default": "default_v2.png",
    }.get(key or "default", "default_v2.png")
    image = xbmcvfs.translatePath(os.path.join(ADDON.getAddonInfo("path"), "resources", "media", filename))
    return {"thumb": image, "thumbnail": image, "poster": image, "icon": image}


def fallback_landscape_art(key="default"):
    filename = {
        "movie": "search_movie_landscape_v2.png",
        "movies": "search_movie_landscape_v2.png",
        "series": "search_series_landscape_v2.png",
        "anime": "search_anime_landscape_v2.png",
        "anime.movie": "search_anime_landscape_v2.png",
        "anime.series": "search_anime_landscape_v2.png",
        "default": "search_default_landscape_v2.png",
    }.get(key or "default", "search_default_landscape_v2.png")
    image = xbmcvfs.translatePath(os.path.join(ADDON.getAddonInfo("path"), "resources", "media", filename))
    return {"thumb": image, "thumbnail": image, "icon": image, "landscape": image, "fanart": image}


def search_result_art(meta, item_type):
    art = meta_art(meta)
    if art:
        return art
    return fallback_art(item_type_art_key(item_type))


def item_type_art_key(item_type):
    if item_type in ("anime", "anime.movie", "anime.series"):
        return "anime"
    if item_type == "series":
        return "series"
    return "movie" if item_type == "movie" else "default"


def first_image(meta, keys):
    for key in keys:
        value = meta.get(key)
        if value:
            return value
    return ""


def episode_art(series_meta, video):
    series_art = meta_art(series_meta)
    episode_thumb = image_url(
        video.get("tvmaze_thumbnail")
        or video.get("thumbnail")
        or video.get("image")
        or video.get("still")
        or video.get("still_path")
        or video.get("screenshot")
    )
    art = {}
    for key in ("fanart", "clearlogo"):
        if series_art.get(key):
            art[key] = series_art.get(key)
    if episode_thumb:
        art.update({"thumb": episode_thumb, "thumbnail": episode_thumb, "poster": episode_thumb, "icon": episode_thumb, "landscape": episode_thumb})
    return art


def season_art(series_meta, season, tvmaze_season=None):
    image = (tvmaze_season or {}).get("image") or {}
    poster = image_url(image.get("original") or image.get("medium"))
    if not poster:
        return meta_art(series_meta)
    art = dict(meta_art(series_meta))
    art.update({"thumb": poster, "thumbnail": poster, "poster": poster, "icon": poster})
    return art


def image_url(value):
    if isinstance(value, dict):
        for key in ("fallback_url", "url", "src"):
            if value.get(key):
                return str(value.get(key))
        return ""
    if not value:
        return ""
    url = str(value)
    fallback_url = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("fallback_url")
    if fallback_url and fallback_url[0]:
        return fallback_url[0]
    return url.replace(" ", "%20")


def meta_info(meta):
    info = {
        "title": meta.get("name") or meta.get("title") or "Untitled",
        "plot": meta.get("description") or meta.get("overview") or meta.get("plot") or "",
    }
    if meta.get("runtime"):
        duration = runtime_seconds(meta.get("runtime"))
        if duration:
            info["duration"] = duration
    if meta.get("released") or meta.get("firstAired") or meta.get("airDate"):
        aired = str(meta.get("released") or meta.get("firstAired") or meta.get("airDate"))[:10]
        info["aired"] = aired
        info["premiered"] = aired
    if meta.get("year"):
        try:
            info["year"] = int(str(meta.get("year"))[:4])
        except (TypeError, ValueError):
            pass
    rating = meta.get("imdbRating") or meta.get("rating")
    if rating:
        try:
            info["rating"] = float(rating)
        except (TypeError, ValueError):
            pass
    votes = safe_int(meta.get("imdbVotes") or meta.get("votes"))
    if votes:
        info["imdbVotes"] = votes
    if meta.get("genres"):
        info["genre"] = meta.get("genres")
    return info


def trakt_enabled_for_lists():
    return trakt.configured() and trakt.authenticated()


def cached_trakt_watched(media_type, ttl=TRAKT_WATCHED_TTL):
    global _API_CACHE_DIRTY
    if not trakt_enabled_for_lists() or not media_type:
        return []
    key = hashlib.sha1(("trakt_watched|%s" % media_type).encode("utf-8")).hexdigest()
    cache = api_cache_data()
    with _API_CACHE_LOCK:
        entry = cache.get("entries", {}).get(key, {})
        if entry and int(time.time()) - safe_int(entry.get("updated")) <= ttl and isinstance(entry.get("data"), list):
            return entry.get("data")
    try:
        data = trakt.watched(media_type)
    except trakt.TraktError as exc:
        xbmc.log("AIOStreams Trakt watched fetch failed for %s: %s" % (media_type, exc), xbmc.LOGWARNING)
        return []
    if not isinstance(data, list):
        data = []
    with _API_CACHE_LOCK:
        cache.setdefault("entries", {})[key] = {"updated": int(time.time()), "ttl": ttl, "data": data}
        _API_CACHE_DIRTY = True
    return data


def trakt_watched_movie_ids():
    ids = set()
    for entry in cached_trakt_watched("movies"):
        movie = entry.get("movie") if isinstance(entry, dict) else {}
        imdb = trakt_imdb_id(movie)
        if imdb:
            ids.add(imdb)
    return ids


def trakt_watched_show_ids():
    ids = set()
    for entry in cached_trakt_watched("shows"):
        show = entry.get("show") if isinstance(entry, dict) else {}
        imdb = trakt_imdb_id(show)
        if imdb:
            ids.add(imdb)
    return ids


def trakt_watched_episode_ids(show_imdb=""):
    ids = set()
    for entry in cached_trakt_watched("shows"):
        show = entry.get("show") if isinstance(entry, dict) else {}
        current_show_imdb = trakt_imdb_id(show)
        if show_imdb and current_show_imdb != show_imdb:
            continue
        for season in entry.get("seasons") or []:
            season_number = safe_int(season.get("number"))
            for episode in season.get("episodes") or []:
                episode_number = safe_int(episode.get("number"))
                if current_show_imdb and season_number and episode_number:
                    ids.add("%s:%d:%d" % (current_show_imdb, season_number, episode_number))
    if show_imdb and not ids and trakt_enabled_for_lists():
        try:
            progress = trakt.show_progress(show_imdb)
        except trakt.TraktError as exc:
            xbmc.log("AIOStreams Trakt watched progress fetch failed for %s: %s" % (show_imdb, exc), xbmc.LOGWARNING)
            return ids
        for season in (progress or {}).get("seasons") or []:
            season_number = safe_int(season.get("number"))
            for episode in season.get("episodes") or []:
                episode_number = safe_int(episode.get("number"))
                if episode.get("completed") and season_number and episode_number:
                    ids.add("%s:%d:%d" % (show_imdb, season_number, episode_number))
    return ids


def trakt_ids(media):
    ids = media.get("ids") if isinstance(media, dict) else {}
    return ids if isinstance(ids, dict) else {}


def trakt_imdb_id(media):
    value = trakt_ids(media).get("imdb")
    return str(value) if value and str(value).startswith("tt") else ""


def trakt_title(media):
    return str((media or {}).get("title") or (media or {}).get("name") or "Untitled")


def trakt_runtime_seconds(media):
    runtime = (media or {}).get("runtime")
    if not runtime:
        return 0
    return runtime_seconds(runtime) if isinstance(runtime, str) else safe_int(runtime) * 60


def trakt_image(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        for key in ("url", "full", "medium", "thumb"):
            if value.get(key):
                return str(value.get(key))
        return ""
    return image_url(value)


def trakt_art(media, fallback_key="default"):
    images = (media or {}).get("images") or {}
    art = {}
    poster = trakt_image(images.get("poster") or (media or {}).get("poster"))
    fanart = trakt_image(images.get("fanart") or images.get("background") or (media or {}).get("fanart"))
    thumb = trakt_image(images.get("thumb") or images.get("screenshot") or (media or {}).get("thumb"))
    logo = trakt_image(images.get("logo") or (media or {}).get("logo"))
    if poster:
        art.update({"thumb": poster, "thumbnail": poster, "poster": poster, "icon": poster})
    if thumb and not art.get("thumb"):
        art.update({"thumb": thumb, "thumbnail": thumb, "icon": thumb, "landscape": thumb})
    if fanart:
        art["fanart"] = fanart
    if logo:
        art["clearlogo"] = logo
    return art or fallback_art(fallback_key)


def trakt_info(media, mediatype, playcount=0, show_title=""):
    info = {
        "title": trakt_title(media),
        "plot": (media or {}).get("overview") or "",
        "mediatype": mediatype,
    }
    if show_title:
        info["tvshowtitle"] = show_title
    runtime = trakt_runtime_seconds(media)
    if runtime:
        info["duration"] = runtime
    if (media or {}).get("year"):
        info["year"] = safe_int((media or {}).get("year"))
    if (media or {}).get("released") or (media or {}).get("first_aired"):
        aired = str((media or {}).get("released") or (media or {}).get("first_aired"))[:10]
        info["aired"] = aired
        info["premiered"] = aired
    if (media or {}).get("rating"):
        info["rating"] = (media or {}).get("rating")
    if (media or {}).get("votes"):
        info["votes"] = (media or {}).get("votes")
    if (media or {}).get("genres"):
        info["genre"] = (media or {}).get("genres")
    if playcount:
        info["playcount"] = playcount
    if mediatype == "episode":
        if (media or {}).get("season") is not None:
            info["season"] = safe_int((media or {}).get("season"))
        if (media or {}).get("number") is not None:
            info["episode"] = safe_int((media or {}).get("number"))
    return info


def trakt_episode_stream_id(entry):
    episode = entry.get("episode") or {}
    show = entry.get("show") or {}
    show_imdb = trakt_imdb_id(show)
    season = safe_int(episode.get("season"))
    number = safe_int(episode.get("number"))
    if show_imdb and season and number:
        return "%s:%d:%d" % (show_imdb, season, number)
    return ""


def trakt_stream_target(entry):
    item_type = entry.get("type") or ""
    if not item_type:
        if isinstance(entry.get("movie"), dict):
            item_type = "movie"
        elif isinstance(entry.get("episode"), dict):
            item_type = "episode"
        elif isinstance(entry.get("show"), dict):
            item_type = "show"
    if item_type == "movie" and isinstance(entry.get("movie"), dict):
        movie = entry.get("movie")
        item_id = trakt_imdb_id(movie)
        if not item_id:
            return {}
        return {
            "type": "movie",
            "id": item_id,
            "title": trakt_title(movie),
            "art": trakt_art(movie, "movie"),
            "info": trakt_info(movie, "movie", safe_int(entry.get("plays"))),
            "duration": trakt_runtime_seconds(movie),
        }
    if item_type == "episode" and isinstance(entry.get("episode"), dict):
        episode = entry.get("episode")
        show = entry.get("show") if isinstance(entry.get("show"), dict) else {}
        item_id = trakt_episode_stream_id(entry)
        if not item_id:
            return {}
        show_title = trakt_title(show) if show else ""
        season = str(episode.get("season") or "")
        number = str(episode.get("number") or "")
        episode_title = trakt_title(episode)
        return {
            "type": "series",
            "id": item_id,
            "video_id": item_id,
            "show_id": trakt_imdb_id(show),
            "show_imdb": trakt_imdb_id(show),
            "season": season,
            "episode": number,
            "title": resume_episode_title(show_title, season, number, episode_title),
            "art": trakt_art(episode, "series"),
            "info": trakt_info(episode, "episode", safe_int(entry.get("plays")), show_title),
            "duration": trakt_runtime_seconds(episode),
        }
    return {}


def trakt_target_media_id(target):
    if target.get("type") == "movie":
        return target.get("id") or ""
    return target.get("show_imdb") or target.get("show_id") or str(target.get("id") or "").split(":")[0]


def enrich_trakt_target(target):
    if not target:
        return target
    target = dict(target)
    item_type = target.get("type") or "movie"
    media_id = trakt_target_media_id(target)
    if not media_id:
        return target
    try:
        response = cached_service_json(join_url(aiometa_base(), "meta", item_type, media_id) + ".json", "aiometadata", META_CACHE_TTL)
    except RuntimeError:
        return target
    meta = response.get("meta") or {}
    if item_type == "movie":
        enriched = enrich_meta("movie", dict(meta, id=media_id))
        target["title"] = enriched.get("name") or enriched.get("title") or target.get("title")
        target["art"] = meta_art(enriched) or target.get("art")
        info = dict(meta_info(enriched))
        info.update({key: value for key, value in (target.get("info") or {}).items() if value not in (None, "", [])})
        target["info"] = info
        target["duration"] = safe_int(info.get("duration")) or safe_int(target.get("duration"))
        return target

    show_art = meta_art(meta)
    episode = None
    for video in meta.get("videos") or []:
        if video_season(video) == str(target.get("season")) and video_episode(video) == str(target.get("episode")):
            episode = video
            break
    if not episode:
        if show_art:
            target["art"] = show_art
        info = dict(target.get("info") or {})
        if not info.get("plot"):
            info["plot"] = meta.get("description") or meta.get("overview") or meta.get("plot") or ""
        if meta.get("name") or meta.get("title"):
            info.setdefault("tvshowtitle", meta.get("name") or meta.get("title"))
        if info:
            target["info"] = info
        return target

    show_title = meta.get("name") or meta.get("title") or ""
    episode_title = episode.get("title") or episode.get("name") or target.get("title")
    target["title"] = resume_episode_title(show_title, target.get("season"), target.get("episode"), episode_title)
    target["art"] = episode_art(meta, episode) or show_art or target.get("art")
    info = episode_info(episode, meta, target.get("season"), target.get("episode"))
    info.update({key: value for key, value in (target.get("info") or {}).items() if value not in (None, "", [])})
    target["info"] = info
    target["duration"] = safe_int(info.get("duration")) or safe_int(target.get("duration"))
    return target


def trakt_progress_position(entry, target):
    duration = safe_int(target.get("duration")) or trakt_runtime_seconds(entry.get("movie") or entry.get("episode") or {})
    progress = entry.get("progress")
    try:
        percent = float(progress or 0)
    except (TypeError, ValueError):
        percent = 0
    if duration <= 0 or percent <= 0:
        return 0
    return int(duration * max(0, min(100, percent)) / 100.0)


def trakt_resume_show_ids(entries):
    show_ids = set()
    for entry in entries:
        show = entry.get("show") if isinstance(entry, dict) else {}
        show_id = trakt_imdb_id(show)
        if show_id:
            show_ids.add(show_id)
            continue
        target = trakt_stream_target(entry)
        if target.get("show_imdb"):
            show_ids.add(target.get("show_imdb"))
    return show_ids


def recent_trakt_history_shows(entries, limit=30):
    shows = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        show = entry.get("show") if isinstance(entry.get("show"), dict) else {}
        show_id = trakt_imdb_id(show)
        if not show_id or show_id in seen:
            continue
        seen.add(show_id)
        shows.append({
            "show": show,
            "show_id": show_id,
            "watched_at": entry.get("watched_at") or "",
        })
        if len(shows) >= limit:
            break
    return shows


def next_episode_from_show_progress(progress):
    if isinstance(progress.get("next_episode"), dict):
        return progress.get("next_episode")
    latest = None
    for season in progress.get("seasons") or []:
        season_number = safe_int(season.get("number"))
        for episode in season.get("episodes") or []:
            if not episode.get("completed"):
                continue
            current = (
                str(episode.get("last_watched_at") or ""),
                season_number,
                safe_int(episode.get("number")),
            )
            if latest is None or current > latest:
                latest = current
    if not latest:
        return {}
    _, season_number, episode_number = latest
    if not season_number or not episode_number:
        return {}
    return {"season": season_number, "number": episode_number + 1, "title": "Episode %s" % (episode_number + 1)}


def trakt_next_target_from_history(item):
    try:
        progress = trakt.show_progress(item.get("show_id"))
    except trakt.TraktError as exc:
        xbmc.log("AIOStreams Trakt next progress failed for %s: %s" % (item.get("show_id"), exc), xbmc.LOGWARNING)
        return None
    episode = next_episode_from_show_progress(progress if isinstance(progress, dict) else {})
    if not episode:
        return None
    entry = {
        "type": "episode",
        "show": item.get("show"),
        "episode": episode,
        "watched_at": item.get("watched_at") or "",
    }
    target = trakt_stream_target(entry)
    if not target.get("id"):
        return None
    return entry.get("watched_at"), target


def trakt_next_entries(history_entries, playback_entries, limit=TRAKT_NEXT_SHOW_LIMIT):
    paused_show_ids = trakt_resume_show_ids(playback_entries)
    seen_targets = set()
    candidates = [
        item for item in recent_trakt_history_shows(history_entries, limit)
        if item.get("show_id") not in paused_show_ids
    ]
    entries = []
    for result in parallel_map(trakt_next_target_from_history, candidates):
        if not result:
            continue
        watched_at, target = result
        target_id = target.get("id")
        if not target_id or target_id in seen_targets:
            continue
        seen_targets.add(target_id)
        entries.append((watched_at, target))
    return [target for _, target in sorted(entries, key=lambda item: item[0], reverse=True)]


def add_trakt_stream_directory(label, target, resume=0, enrich=True, context_menu=None, replace_context=False):
    if enrich:
        target = enrich_trakt_target(target)
    label = target.get("title") or label
    params = {
        "action": "streams",
        "type": target.get("type", "movie"),
        "id": target.get("id", ""),
        "title": target.get("title", ""),
        "art": art_json(target.get("art") or {}),
        "resume": str(safe_int(resume)),
        "duration": str(safe_int(target.get("duration"))),
        "video_id": target.get("video_id", ""),
        "show_id": target.get("show_id", ""),
        "show_imdb": target.get("show_imdb", ""),
        "season": target.get("season", ""),
        "episode": target.get("episode", ""),
    }
    add_directory(label, params, target.get("art"), target.get("info"), context_menu=context_menu, replace_context=replace_context)


def add_trakt_show_directory(entry, playcount=0):
    show = entry.get("show") if isinstance(entry.get("show"), dict) else {}
    item_id = trakt_imdb_id(show)
    if not item_id:
        return False
    add_directory(trakt_title(show), {
        "action": "details",
        "type": "series",
        "id": item_id,
    }, trakt_art(show, "series"), trakt_info(show, "tvshow", playcount))
    return True


def enrich_meta(item_type, meta):
    if meta_art(meta) and meta_info(meta).get("plot"):
        return meta
    item_id = meta.get("id")
    if not item_id:
        return meta
    try:
        response = cached_service_json(join_url(aiometa_base(), "meta", item_type, item_id) + ".json", "aiometadata", META_CACHE_TTL)
    except RuntimeError:
        return meta
    full_meta = response.get("meta") or {}
    merged = dict(full_meta)
    merged.update({key: value for key, value in meta.items() if value not in (None, "", [])})
    prefer_full_meta_art(merged, full_meta)
    return merged


def prefer_full_meta_art(merged, full_meta):
    for key in (
        "poster",
        "posterUrl",
        "background",
        "fanart",
        "backdrop",
        "backdropUrl",
        "landscapePoster",
        "logo",
        "clearLogo",
    ):
        if full_meta.get(key):
            merged[key] = full_meta.get(key)


def enrich_episode_meta(item_type, video):
    if meta_art(video) and meta_info(video).get("plot"):
        return video
    video_id = video.get("id")
    if not video_id:
        return video
    try:
        response = cached_service_json(join_url(aiometa_base(), "meta", item_type, video_id) + ".json", "aiometadata", META_CACHE_TTL)
    except RuntimeError:
        return video
    full_meta = response.get("meta") or {}
    merged = dict(full_meta)
    merged.update({key: value for key, value in video.items() if value not in (None, "", [])})
    return merged


def list_root():
    xbmcplugin.setPluginCategory(HANDLE, "AIOStreams")
    if not aiostreams_base():
        add_directory("Settings", {"action": "settings"}, fallback_art("settings"))
        set_view("videos", "view_root")
        return

    try:
        manifest = get_manifest()
    except RuntimeError as exc:
        error(str(exc))
        add_directory("Settings", {"action": "settings"}, fallback_art("settings"))
        set_view("videos", "view_root")
        return

    add_directory("Search", {"action": "search", "type": ""}, fallback_art("search"))
    add_directory("Resume", {"action": "resume"}, fallback_art("sources"))
    if trakt_enabled_for_lists():
        add_directory("Trakt", {"action": "trakt"}, fallback_art("sources"))

    catalogs = root_catalogs(manifest)
    duplicate_labels = duplicate_catalog_labels(catalogs)
    for catalog in catalogs:
        label = catalog_label(catalog, duplicate_labels)
        add_directory(label, {
            "action": "catalog",
            "type": catalog.get("type", "movie"),
            "id": catalog.get("id", ""),
            "skip": "0",
        }, catalog_art(catalog))
    add_directory("Settings", {"action": "settings"}, fallback_art("settings"))
    set_view("videos", "view_root")


def root_catalogs(manifest):
    catalogs = manifest.get("catalogs", [])
    home_catalogs = [catalog for catalog in catalogs if catalog.get("showInHome")]
    if home_catalogs:
        return home_catalogs
    return [catalog for catalog in catalogs if catalog_is_browseable(catalog)]


def catalog_is_browseable(catalog):
    required = catalog.get("extraRequired") or [extra.get("name") for extra in catalog.get("extra", []) if extra.get("isRequired")]
    for name in required:
        extra = catalog_extra_definition(catalog, name)
        if not extra.get("options"):
            return False
    return True


def catalog_extra_definition(catalog, name):
    for extra in catalog.get("extra", []):
        if extra.get("name") == name:
            return extra
    return {}


def duplicate_catalog_labels(catalogs):
    counts = {}
    for catalog in catalogs:
        label = catalog.get("name") or catalog.get("id") or ""
        counts[label] = counts.get(label, 0) + 1
    return set(label for label, count in counts.items() if count > 1)


def catalog_label(catalog, duplicate_labels):
    label = catalog.get("name") or catalog.get("id")
    if label not in duplicate_labels:
        return label
    return "%s (%s)" % (label, catalog_type_label(catalog.get("type")))


def catalog_type_label(catalog_type):
    labels = {
        "movie": "Movies",
        "series": "Series",
        "anime.movie": "Anime Movies",
        "anime.series": "Anime Series",
        "anime": "Anime",
    }
    return labels.get(catalog_type, str(catalog_type or "Other").title())


def catalog_art(catalog):
    return fallback_art(catalog_art_key(catalog))


def catalog_art_key(catalog):
    catalog_id = str(catalog.get("id") or "").lower()
    name = str(catalog.get("name") or "").lower()
    catalog_type = catalog.get("type")
    if catalog_type in ("anime", "anime.movie", "anime.series"):
        return "anime"
    suffix = "series" if catalog_type == "series" else "movies" if catalog_type == "movie" else "default"
    if suffix == "default":
        return "default"
    if "trending" in catalog_id or "trending" in name:
        return "trending_" + suffix
    if "top_rated" in catalog_id or "top rated" in name:
        return "top_" + suffix
    if "popular" in name or ".top_" in catalog_id or catalog_id.endswith(".top"):
        return "popular_" + suffix
    return suffix


def catalog_extra(catalog, genre, skip, search_query=None):
    extras = []
    if genre:
        extras.append(("genre", genre))
    if search_query:
        extras.append(("search", search_query))
    if skip:
        extras.append(("skip", str(skip)))
    if not extras:
        return ""
    return "&".join(
        "%s=%s" % (urllib.parse.quote(str(key), safe=""), urllib.parse.quote(str(value), safe=""))
        for key, value in extras
    )


def list_catalog(catalog_type, catalog_id, genre="", skip=0, search_query=""):
    catalog = find_catalog(catalog_type, catalog_id)
    label = catalog.get("name") or catalog_id
    xbmcplugin.setPluginCategory(HANDLE, label)

    genre_extra = None
    for extra_item in catalog.get("extra", []):
        if extra_item.get("name") == "genre" and extra_item.get("options"):
            genre_extra = extra_item
            break

    if genre_extra and not genre and not search_query and genre_extra.get("isRequired"):
        for option in genre_extra.get("options", []):
            add_directory(str(option), {
                "action": "catalog",
                "type": catalog_type,
                "id": catalog_id,
                "genre": str(option),
                "skip": "0",
            }, catalog_art(catalog))
        set_view()
        return

    metas = []
    page_count = 1 if search_query else max(1, min(setting_int("catalog_pages_per_page", 3), 10))
    current_skip = skip
    reached_end = False
    for page_index in range(page_count):
        extra_path = catalog_extra(catalog, genre, current_skip, search_query)
        url = join_url(aiometa_base(), "catalog", catalog_type, catalog_id)
        if extra_path:
            url += "/" + extra_path
        url += ".json"

        try:
            response = cached_service_json(url, "aiometadata", META_CACHE_TTL)
        except RuntimeError as exc:
            if page_index == 0:
                error(str(exc))
                set_view("movies", "view_results")
                return
            break

        page_metas = response.get("metas", [])
        if not page_metas:
            reached_end = True
            break
        metas.extend(page_metas)
        current_skip += len(page_metas)

    typed_metas = []
    for meta in metas:
        item_type = meta.get("type") or catalog_type
        item_id = meta.get("id")
        if not item_id:
            continue
        typed_metas.append((item_type, item_id, meta))
    enriched_metas = parallel_map(lambda entry: enrich_meta(entry[0], entry[2]), typed_metas)
    watched_by_type = {
        "movie": trakt_watched_movie_ids() if any(entry[0] == "movie" for entry in typed_metas) else set(),
        "series": trakt_watched_show_ids() if any(entry[0] == "series" for entry in typed_metas) else set(),
    }
    for (item_type, item_id, _meta), enriched in zip(typed_metas, enriched_metas):
        add_video_item(enriched, item_type, item_id, watched_ids=watched_by_type.get(item_type))

    if metas and not search_query and not reached_end:
        add_directory("Next page", {
            "action": "catalog",
            "type": catalog_type,
            "id": catalog_id,
            "genre": genre,
            "skip": str(current_skip),
        }, catalog_art(catalog))
    set_view("movies", "view_results")


def list_details(item_type, item_id, resume=0, duration=0, target_season="", target_episode="", resume_key=""):
    url = join_url(aiometa_base(), "meta", item_type, item_id) + ".json"
    try:
        response = cached_service_json(url, "aiometadata", META_CACHE_TTL)
    except RuntimeError as exc:
        error(str(exc))
        set_view()
        return

    meta = response.get("meta") or {}
    title = meta.get("name") or meta.get("title") or item_id
    xbmcplugin.setPluginCategory(HANDLE, title)

    videos = meta.get("videos") or []
    if videos:
        queue_imdb_show_scores(item_type, item_id)
        seasons = []
        for video in videos:
            season = video_season(video)
            if season is None:
                continue
            if season not in seasons:
                seasons.append(season)
        tvmaze_seasons = tvmaze_season_fallbacks(meta, item_id) if item_type == "series" else {}
        for season in sorted(seasons, key=season_sort_key):
            add_directory("Season %s" % season, {
                "action": "episodes",
                "type": item_type,
                "id": item_id,
                "season": season,
                "resume": str(safe_int(resume)) if str(season) == str(target_season) else "0",
                "duration": str(safe_int(duration)) if str(season) == str(target_season) else "0",
                "episode": str(target_episode) if str(season) == str(target_season) else "",
                "resume_key": str(resume_key) if str(season) == str(target_season) else "",
            }, season_art(meta, season, tvmaze_seasons.get(str(season))), season_info(meta, season))
    else:
        add_directory("Sources", {
            "action": "streams",
            "type": item_type,
            "id": best_stream_id(meta, item_id),
            "title": title,
            "art": art_json(meta_art(meta)),
            "resume": str(safe_int(resume)),
            "duration": str(safe_int(duration)),
            "resume_key": str(resume_key),
        }, meta_art(meta), meta_info(meta))

    set_view("seasons" if videos else "movies", "view_seasons" if videos else "view_results")


def list_episodes(item_type, item_id, season, resume=0, duration=0, target_episode="", resume_key=""):
    url = join_url(aiometa_base(), "meta", item_type, item_id) + ".json"
    try:
        response = cached_service_json(url, "aiometadata", META_CACHE_TTL)
    except RuntimeError as exc:
        error(str(exc))
        set_view("episodes")
        return

    meta = response.get("meta") or {}
    videos = [video for video in meta.get("videos") or [] if video_season(video) == str(season)]
    imdb_id = tvmaze_imdb_id(meta, item_id) if item_type == "series" else ""
    watched_episode_ids = trakt_watched_episode_ids(imdb_id) if imdb_id else set()
    imdb_scores = load_imdb_episode_scores() if imdb_episode_scores_enabled() and imdb_id else {"shows": {}}
    if imdb_episode_scores_enabled() and imdb_id and imdb_cache_stale(imdb_scores, imdb_id, season, videos):
        queue_imdb_season_scores(item_type, item_id, season)
    tvmaze_fallbacks = {}
    if item_type == "series" and any(episode_needs_fallback(video) for video in videos):
        tvmaze_fallbacks = tvmaze_episode_fallbacks(meta, item_id, season)

    merged_videos = [
        merge_episode_fallback(video, tvmaze_fallbacks.get(video_episode(video) or ""))
        for video in videos
    ]
    enriched_videos = parallel_map(lambda video: enrich_episode_meta(item_type, video), merged_videos)
    for video in enriched_videos:
        video = apply_imdb_episode_score(video, imdb_scores, imdb_id, season)
        video_id = video.get("id")
        if not video_id:
            continue
        episode = video_episode(video) or ""
        episode_title = video.get("title") or video.get("name") or "Episode %s" % episode
        label = episode_title
        if episode and not str(label).lower().startswith("episode"):
            label = "%s. %s" % (episode, label)
        show_title = meta.get("name") or meta.get("title") or item_id
        content_title = resume_episode_title(show_title, season, episode, episode_title)
        info = episode_info(video, meta, season, episode)
        if imdb_id and episode and ("%s:%s:%s" % (imdb_id, season, episode)) in watched_episode_ids:
            info["playcount"] = 1
        if str(episode).isdigit():
            info["episode"] = int(episode)
        if str(season).isdigit():
            info["season"] = int(season)
        show_imdb = tvmaze_imdb_id(meta, item_id) if item_type == "series" else ""
        item_art = episode_art(meta, video)
        add_directory(label, {
            "action": "streams",
            "type": item_type,
            "id": best_stream_id(video, video_id),
            "video_id": video_id,
            "show_id": item_id,
            "show_imdb": show_imdb,
            "season": season,
            "episode": episode,
            "title": content_title,
            "art": art_json(item_art),
            "resume": str(safe_int(resume)) if not target_episode or str(episode) == str(target_episode) else "0",
            "duration": str(safe_int(duration)) if not target_episode or str(episode) == str(target_episode) else "0",
            "resume_key": str(resume_key) if not target_episode or str(episode) == str(target_episode) else "",
        }, item_art, info)
    set_view("episodes", "view_episodes")


def apply_imdb_episode_score(video, imdb_scores, imdb_id, season):
    if not imdb_id:
        return video
    score = imdb_cached_episode_score(imdb_scores, imdb_id, season, video_episode(video) or "")
    if not score:
        return video
    merged = dict(video)
    merged["imdbRating"] = score.get("rating")
    if score.get("votes"):
        merged["imdbVotes"] = score.get("votes")
    if score.get("episode_id"):
        merged["imdbEpisodeId"] = score.get("episode_id")
    return merged


def refresh_imdb_show_scores(item_type, item_id, quiet=False):
    if not imdb_episode_scores_enabled() or item_type != "series":
        return
    url = join_url(aiometa_base(), "meta", item_type, item_id) + ".json"
    try:
        response = cached_service_json(url, "aiometadata", META_CACHE_TTL)
    except RuntimeError as exc:
        xbmc.log("AIOStreams IMDb show score refresh could not load metadata for %s: %s" % (item_id, exc), xbmc.LOGWARNING)
        return
    meta = response.get("meta") or {}
    imdb_id = tvmaze_imdb_id(meta, item_id)
    if not imdb_id:
        return
    data = load_imdb_episode_scores()
    seasons = {}
    for video in meta.get("videos") or []:
        video_season_value = video_season(video)
        if video_season_value is None:
            continue
        seasons.setdefault(str(video_season_value), []).append(video)
    refreshed = 0
    for season_value, season_videos in sorted(seasons.items(), key=lambda item: season_sort_key(item[0])):
        if imdb_cache_stale(data, imdb_id, season_value, season_videos):
            if refresh_imdb_season_scores(imdb_id, season_value):
                refreshed += 1
            data = load_imdb_episode_scores()
    if refreshed and not quiet:
        notify("IMDb episode scores refreshed")


def episode_info(video, series_meta, season, episode):
    info = meta_info(video)
    info["mediatype"] = "episode"
    show_title = series_meta.get("name") or series_meta.get("title") or ""
    episode_title = video.get("title") or video.get("name") or "Episode %s" % episode
    if show_title:
        info["tvshowtitle"] = show_title
    info["title"] = episode_title
    if not info.get("plot"):
        info["plot"] = video.get("overview") or video.get("description") or video.get("plot") or ""
    if info.get("plot") and not info.get("plotoutline"):
        info["plotoutline"] = info.get("plot")
    if str(episode).isdigit():
        info["episode"] = int(episode)
    if str(season).isdigit():
        info["season"] = int(season)
    return info


def season_info(series_meta, season):
    title = "Season %s" % season
    info = {
        "title": title,
        "mediatype": "season",
        "tvshowtitle": series_meta.get("name") or series_meta.get("title") or "",
        "plot": series_meta.get("description") or series_meta.get("overview") or series_meta.get("plot") or "",
    }
    if str(season).isdigit():
        info["season"] = int(season)
    return info


def resume_episode_title(show_title, season, episode, episode_title):
    title = episode_title or ("Episode %s" % episode if episode else "Episode")
    if str(season).isdigit() and str(episode).isdigit():
        code = "S%02dE%02d" % (int(season), int(episode))
        return "%s - %s - %s" % (show_title, code, title) if show_title else "%s - %s" % (code, title)
    if show_title:
        return "%s - %s" % (show_title, title)
    return title


def display_rating(value):
    if is_missing_rating(value):
        return ""
    try:
        return "%.1f" % float(value)
    except (TypeError, ValueError):
        return str(value)


def episode_needs_fallback(video):
    return not episode_plot(video) or not meta_art(video)


def episode_plot(video):
    return video.get("overview") or video.get("description") or video.get("plot") or ""


def tvmaze_episode_fallbacks(series_meta, item_id, season):
    imdb_id = tvmaze_imdb_id(series_meta, item_id)
    if not imdb_id:
        return {}
    try:
        show = get_json("https://api.tvmaze.com/lookup/shows?imdb=" + urllib.parse.quote(imdb_id))
        show_id = show.get("id")
        if not show_id:
            return {}
        episodes = get_json("https://api.tvmaze.com/shows/%s/episodes" % show_id)
    except RuntimeError:
        return {}

    fallbacks = {}
    for episode in episodes:
        if str(episode.get("season")) != str(season):
            continue
        number = episode.get("number")
        if number is None:
            continue
        fallback = tvmaze_episode_data(episode)
        if fallback:
            fallbacks[str(number)] = fallback
    return fallbacks


def tvmaze_season_fallbacks(series_meta, item_id):
    imdb_id = tvmaze_imdb_id(series_meta, item_id)
    if not imdb_id:
        return {}
    try:
        show = get_json("https://api.tvmaze.com/lookup/shows?imdb=" + urllib.parse.quote(imdb_id))
        show_id = show.get("id")
        if not show_id:
            return {}
        seasons = get_json("https://api.tvmaze.com/shows/%s/seasons" % show_id)
    except RuntimeError:
        return {}

    fallbacks = {}
    for season in seasons:
        number = season.get("number")
        if number is None:
            continue
        fallbacks[str(number)] = season
    return fallbacks


def tvmaze_imdb_id(series_meta, item_id):
    for value in (
        series_meta.get("imdb_id"),
        series_meta.get("imdbId"),
        series_meta.get("imdb"),
        item_id,
    ):
        if not value:
            continue
        imdb_id = str(value).split(":")[0]
        if imdb_id.startswith("tt"):
            return imdb_id
    return ""


def tvmaze_episode_data(episode):
    data = {}
    if episode.get("name"):
        data["name"] = episode.get("name")
    summary = clean_html(episode.get("summary"))
    if summary:
        data["overview"] = summary
        data["description"] = summary
    image = episode.get("image") or {}
    thumbnail = image.get("original") or image.get("medium")
    if thumbnail:
        data["thumbnail"] = thumbnail
    airdate = episode.get("airdate") or str(episode.get("airstamp") or "")[:10]
    if airdate:
        data["firstAired"] = airdate
        data["released"] = airdate
    rating = (episode.get("rating") or {}).get("average")
    if rating:
        data["rating"] = str(rating)
    if episode.get("runtime"):
        data["runtime"] = episode.get("runtime")
    return data


def merge_episode_fallback(video, fallback):
    if not fallback:
        return video
    merged = dict(video)
    if is_empty(merged.get("name")) and fallback.get("name"):
        merged["name"] = fallback.get("name")
    if not episode_plot(merged):
        for key in ("overview", "description", "plot"):
            if fallback.get(key):
                merged[key] = fallback.get(key)
    if fallback.get("thumbnail"):
        merged["tvmaze_thumbnail"] = fallback.get("thumbnail")
        if not meta_art(merged):
            merged["thumbnail"] = fallback.get("thumbnail")
    for key in ("firstAired", "released", "runtime"):
        if is_empty(merged.get(key)) and fallback.get(key):
            merged[key] = fallback.get(key)
    if is_missing_rating(merged.get("rating")) and fallback.get("rating"):
        merged["rating"] = fallback.get("rating")
    return merged


def clean_html(value):
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", "", str(value))
    return " ".join(html.unescape(text).split())


def is_empty(value):
    return value is None or value == "" or value == []


def is_missing_rating(value):
    return value in (None, "", 0, 0.0, "0", "0.0")


def video_season(video):
    value = video.get("season")
    if value is not None and value != "":
        return str(value)
    parsed = parse_stremio_episode_id(video.get("id"))
    return parsed[0] if parsed else None


def video_episode(video):
    value = video.get("episode") or video.get("number")
    if value is not None and value != "":
        return str(value)
    parsed = parse_stremio_episode_id(video.get("id"))
    return parsed[1] if parsed else None


def parse_stremio_episode_id(video_id):
    if not video_id:
        return None
    parts = str(video_id).split(":")
    if len(parts) >= 3 and parts[-2].isdigit() and parts[-1].isdigit():
        return parts[-2], parts[-1]
    return None


def best_stream_id(meta, fallback):
    for key in ("imdb_id", "imdbId", "imdb", "imdbid"):
        value = meta.get(key)
        if value and str(value).startswith("tt"):
            return str(value)
    return fallback


def stream_label(stream, index):
    stream_data = stream.get("streamData") or {}
    behavior = stream.get("behaviorHints") or {}
    release_name = stream_data.get("filename") or behavior.get("filename") or stream.get("title") or stream.get("description") or "Source %s" % index
    return str(release_name).replace("\n", " | ")


def stream_plot(stream):
    stream_data = stream.get("streamData") or {}
    behavior = stream.get("behaviorHints") or {}
    parts = []

    for label, value in stream_info_fields(stream):
        parts.append("%s: %s" % (label, value))

    filename = stream_data.get("filename") or behavior.get("filename")
    folder_name = stream_data.get("folderName")
    if folder_name and folder_name != filename:
        parts.append("Folder: %s" % folder_name)
    return " | ".join(parts)


def stream_info_fields(stream):
    stream_data = stream.get("streamData") or {}
    parsed = stream_data.get("parsedFile") or {}
    behavior = stream.get("behaviorHints") or {}
    fields = []

    for label, value in (
        ("Indexer", stream_data.get("indexer")),
    ):
        if value:
            fields.append((label, str(value)))

    quality = unique_values([
        parsed.get("resolution"),
        parsed.get("quality"),
        parsed.get("encode"),
        list_text(parsed.get("visualTags")),
    ])
    if quality:
        fields.append(("Quality", " | ".join(quality)))

    audio = list_text(parsed.get("audioTags"))
    if audio:
        fields.append(("Audio", audio))

    languages = list_text(parsed.get("languages"))
    if languages:
        fields.append(("Language", languages))

    if parsed.get("releaseGroup"):
        fields.append(("Group", str(parsed.get("releaseGroup"))))

    size = stream_data.get("size") or behavior.get("videoSize")
    formatted_size = format_size(size)
    if formatted_size:
        fields.append(("Size", formatted_size))

    seeders = deep_get(stream_data, ["torrent", "seeders"])
    if seeders is not None:
        fields.append(("Seeders", str(seeders)))
    if stream_data.get("library"):
        fields.append(("Library", "yes"))
    if stream_data.get("proxied"):
        fields.append(("Proxied", "yes"))

    return fields


def list_text(values):
    if isinstance(values, list):
        return ", ".join(str(value) for value in values[:4] if value)
    return str(values) if values else ""


def unique_values(values):
    seen = set()
    result = []
    for value in values:
        if value is None or value == "":
            continue
        text = str(value)
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def deep_get(data, keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def format_size(value):
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index < 3:
        return "%d %s" % (round(size), units[unit_index])
    return "%.1f %s" % (size, units[unit_index])


def season_sort_key(value):
    text = str(value)
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def stream_headers(stream):
    headers = {}
    behavior = stream.get("behaviorHints") or {}
    proxy_headers = behavior.get("proxyHeaders") or {}
    if isinstance(proxy_headers.get("request"), dict):
        headers.update(proxy_headers.get("request"))
    if isinstance(stream.get("headers"), dict):
        headers.update(stream.get("headers"))
    return sanitize_playback_headers(headers)


def sanitize_playback_headers(headers):
    if not isinstance(headers, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in headers.items()
        if key and value is not None and str(key).lower() not in PLAYBACK_HEADER_DENYLIST
    }


def list_streams(item_type, item_id, content_title="", content_art_json="", video_id="", show_id="", show_imdb="", season="", episode="", resume=0, duration=0, resume_key=""):
    base = aiostreams_base()
    if not base:
        error("AIOStreams manifest URL is not configured")
        set_view()
        return
    url = join_url(base, "stream", item_type, item_id) + ".json"
    try:
        response = get_json(service_url(url, "aiostreams"))
    except RuntimeError as exc:
        if open_stream_search_fallback(item_type, content_title or item_id, season, episode, resume, duration, resume_key, str(exc)):
            return
        error(str(exc))
        set_view()
        return

    xbmcplugin.setPluginCategory(HANDLE, "Sources")
    streams = response.get("streams", [])
    content_art = art_from_json(content_art_json)
    playable_count = 0
    stream_contexts = load_stream_contexts()
    for index, stream in enumerate(streams, start=1):
        stream_url = stream.get("url") or stream.get("externalUrl")
        label = stream_label(stream, index)
        info = {"title": label, "plot": stream_plot(stream)}
        if stream_url and stream_url.startswith(("http://", "https://")):
            playable_count += 1
            context = {
                "url": stream_url,
                "headers": stream_headers(stream),
                "item_id": item_id,
                "item_type": item_type,
                "video_id": video_id,
                "show_id": show_id,
                "show_imdb": show_imdb,
                "season": season,
                "episode": episode,
                "resume": safe_int(resume),
                "duration": safe_int(duration),
                "title": content_title or item_id,
                "stream_title": label,
                "art": content_art,
            }
            if resume_key:
                context["key"] = resume_key
            token = register_stream_context(context, stream_contexts, save=False)
            add_playable(label, {
                "action": "play_token",
                "token": token,
            }, art=fallback_art("sources"), info=info)
        else:
            add_directory("Unsupported: " + label, {"action": "noop"}, art=fallback_art("sources"), info=info)

    if not playable_count:
        if open_stream_search_fallback(item_type, content_title or item_id, season, episode, resume, duration, resume_key, "no direct playable HTTP streams"):
            return
        notify("No direct playable HTTP streams returned")
    else:
        save_stream_contexts(stream_contexts)
    set_view("videos", "view_sources", cache_to_disc=False)


def kodi_header_url(url, headers):
    if not headers:
        return url
    return url + "|" + urllib.parse.urlencode(headers)


def playback_url_and_headers(original_url, resolved_url, headers):
    resolved_parts = urllib.parse.urlparse(resolved_url)
    playback_headers = sanitize_playback_headers(headers)

    if resolved_parts.username:
        username = urllib.parse.unquote(resolved_parts.username or "")
        password = urllib.parse.unquote(resolved_parts.password or "")
        if username:
            token = base64.b64encode(("%s:%s" % (username, password)).encode("utf-8")).decode("ascii")
            playback_headers["Authorization"] = "Basic " + token
        host = resolved_parts.hostname or ""
        if resolved_parts.port:
            host = "%s:%s" % (host, resolved_parts.port)
        resolved_url = urllib.parse.urlunparse((
            resolved_parts.scheme,
            host,
            resolved_parts.path,
            resolved_parts.params,
            resolved_parts.query,
            resolved_parts.fragment,
        ))

    return resolved_url, playback_headers


def kodi_direct_url_and_headers(resolved_url, headers):
    return resolved_url, sanitize_playback_headers(headers)


def kodi_builtin_headers(headers):
    # Cache-busters and Connection: close work around Kodi's CCurlFile
    # handle-pool reuse; they must not leak into the forwarder's upstream
    # requests where they would defeat keep-alive across Range retries.
    merged = dict(headers)
    merged.setdefault("Cache-Control", "no-cache")
    merged.setdefault("Pragma", "no-cache")
    merged.setdefault("Connection", "close")
    return merged


def playback_mime(url, title=""):
    text = urllib.parse.unquote((urllib.parse.urlparse(url).path or "") + " " + (title or "")).lower()
    for suffix, mime in (
        (".mkv", "video/x-matroska"),
        (".mp4", "video/mp4"),
        (".m4v", "video/mp4"),
        (".avi", "video/x-msvideo"),
        (".ts", "video/mp2t"),
        (".m2ts", "video/mp2t"),
        (".mov", "video/quicktime"),
        (".webm", "video/webm"),
    ):
        if suffix in text:
            return mime
    if any(marker in text for marker in ("remux", "bluray", "bdrip", "webrip", "web-dl")):
        return "video/x-matroska"
    return ""


def play(url, headers_json, resume_seconds=0, context=None):
    context = context or {}
    if not url:
        if context:
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
            if open_resume_fallback(context, resume_seconds, safe_int(context.get("duration")), "missing playback URL"):
                return
        error("Playback URL is missing")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    try:
        headers = json.loads(headers_json or "{}")
    except ValueError:
        headers = {}
    headers = sanitize_playback_headers(headers)
    try:
        resolved_url = resolve_playback_redirect(url, headers)
    except RuntimeError as exc:
        xbmc.log("AIOStreams playback resolve failed for %s: %s" % (redact_url(url), exc), xbmc.LOGERROR)
        if context:
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
            if open_resume_fallback(context, resume_seconds, safe_int(context.get("duration")), str(exc)):
                return
        error(str(exc))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    playback_url, playback_headers = playback_url_and_headers(url, resolved_url, headers)
    direct_url, direct_headers = kodi_direct_url_and_headers(resolved_url, headers)
    resolved_host = urllib.parse.urlparse(playback_url).netloc
    mime = playback_mime(playback_url, context.get("stream_title") or context.get("title") or "")
    xbmc.log("AIOStreams resolved playback host: %s, headers: %s, mime: %s" % (
        resolved_host or "unknown",
        ",".join(sorted(playback_headers.keys())) or "none",
        mime or "auto",
    ), xbmc.LOGINFO)
    if setting_bool("use_local_forwarder", False):
        try:
            item_path = register_forwarder_url(playback_url, playback_headers, context)
            xbmc.log("AIOStreams playback engine: local-forwarder", xbmc.LOGINFO)
        except Exception as exc:
            xbmc.log("AIOStreams local forwarder registration failed, using direct playback: %s" % exc, xbmc.LOGWARNING)
            item_path = kodi_header_url(direct_url, kodi_builtin_headers(direct_headers))
            xbmc.log("AIOStreams playback engine: kodi-builtin", xbmc.LOGINFO)
    else:
        item_path = kodi_header_url(direct_url, kodi_builtin_headers(direct_headers))
        xbmc.log("AIOStreams playback engine: kodi-builtin", xbmc.LOGINFO)
    item = xbmcgui.ListItem(path=item_path)
    item.setPath(item_path)
    item.setProperty("IsPlayable", "true")
    if mime:
        item.setMimeType(mime)
        item.setContentLookup(False)
    if resume_seconds and resume_seconds > 30:
        item.setProperty("StartOffset", str(int(resume_seconds)))
        item.setProperty("ResumeTime", str(int(resume_seconds)))
        duration = safe_int(context.get("duration"))
        if duration:
            item.setProperty("TotalTime", str(duration))
        try:
            video_tag = item.getVideoInfoTag()
            if duration and hasattr(video_tag, "setResumePoint"):
                video_tag.setResumePoint(float(resume_seconds), float(duration))
        except Exception:
            pass
    if context.get("title"):
        set_item_info(item, {"title": context.get("title")})
    if context:
        save_current_playback(dict(context, url=url, headers=headers, resume=resume_seconds))
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def play_token(token, resume_seconds=0):
    context = load_stream_context(token)
    if not context:
        error("Playback link expired; open Sources again")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    url = context.get("url") or ""
    headers = context.get("headers") if isinstance(context.get("headers"), dict) else {}
    if not resume_seconds:
        resume_seconds = safe_int(context.get("resume"))
    play(url, json.dumps(headers), resume_seconds, context)


def should_keep_resume(position, duration):
    if position < 60:
        return False
    if duration and duration > 0 and position >= duration * 0.9:
        return False
    return True


def direct_playback_url(url):
    return str(url or "").startswith(("http://", "https://"))


def resume_entry_type(entry):
    item_type = str(entry.get("item_type") or "")
    if item_type in ("movie", "series"):
        return item_type
    if entry.get("season") or entry.get("episode") or entry.get("show_id") or entry.get("show_imdb"):
        return "series"
    if parse_stremio_episode_id(entry.get("video_id") or entry.get("item_id")):
        return "series"
    return "movie"


def resume_entry_stream_id(entry, item_type):
    item_id = str(entry.get("item_id") or "")
    video_id = str(entry.get("video_id") or "")
    if item_type != "series":
        return item_id or video_id

    for value in (item_id, video_id):
        if value and parse_stremio_episode_id(value):
            return value

    season = str(entry.get("season") or "")
    episode = str(entry.get("episode") or "")
    if season and episode:
        base = str(entry.get("show_imdb") or entry.get("show_id") or item_id or "")
        if base:
            return "%s:%s:%s" % (base, season, episode)
    return item_id or video_id


def resume_search_query(entry):
    title = str(entry.get("title") or entry.get("stream_title") or "").strip()
    if resume_entry_type(entry) == "series":
        match = re.match(r"(.+?)\s+-\s+S\d+E\d+\s+-\s+.+", title, re.I)
        if match:
            return match.group(1).strip()
    return title


def resume_sources_params(entry, position=0, duration=0, art=None):
    item_type = resume_entry_type(entry)
    stream_id = resume_entry_stream_id(entry, item_type)
    title = str(entry.get("title") or entry.get("stream_title") or stream_id or "").strip()
    entry_art = art if isinstance(art, dict) else art_from_json(entry.get("art"))
    position = safe_int(position or entry.get("resume") or entry.get("position"))
    duration = safe_int(duration or entry.get("duration"))
    season = str(entry.get("season") or "")
    episode = str(entry.get("episode") or "")
    resume_key = str(entry.get("key") or entry.get("resume_key") or "")

    if stream_id:
        return {
            "action": "streams",
            "type": item_type,
            "id": stream_id,
            "title": title,
            "art": art_json(entry_art),
            "resume": str(position),
            "duration": str(duration),
            "video_id": entry.get("video_id") or "",
            "show_id": entry.get("show_id") or "",
            "show_imdb": entry.get("show_imdb") or "",
            "season": season,
            "episode": episode,
            "resume_key": resume_key,
        }

    query = resume_search_query(entry)
    if not query:
        return {}
    return {
        "action": "search",
        "type": item_type,
        "query": query,
        "resume": str(position),
        "duration": str(duration),
        "season": season,
        "episode": episode,
        "resume_key": resume_key,
    }


def open_resume_fallback(entry, position=0, duration=0, reason=""):
    params = resume_sources_params(entry, position, duration)
    if not params:
        return False
    if reason:
        xbmc.log("AIOStreams opening resume source fallback: %s" % reason, xbmc.LOGWARNING)
    xbmc.executebuiltin("Container.Update(%s)" % addon_url(**params))
    return True


def stream_search_query(item_type, title):
    title = str(title or "").strip()
    if item_type == "series":
        match = re.match(r"(.+?)\s+-\s+S\d+E\d+\s+-\s+.+", title, re.I)
        if match:
            return match.group(1).strip()
    return title


def open_stream_search_fallback(item_type, title, season="", episode="", resume=0, duration=0, resume_key="", reason=""):
    if not resume:
        return False
    query = stream_search_query(item_type, title)
    if not query:
        return False
    if reason:
        xbmc.log("AIOStreams opening stream search fallback: %s" % reason, xbmc.LOGWARNING)
    xbmc.executebuiltin("Container.Update(%s)" % addon_url(
        action="search",
        type=item_type,
        query=query,
        resume=str(safe_int(resume)),
        duration=str(safe_int(duration)),
        season=str(season or ""),
        episode=str(episode or ""),
        resume_key=str(resume_key or ""),
    ))
    return True


def resume_context_menu(entry):
    key = entry.get("key") or ""
    if not key:
        return []
    return [("Remove from Resume", "RunPlugin(%s)" % addon_url(action="resume_mark_watched", key=key))]


def trakt_resume_context_menu(entry):
    playback_id = entry.get("id") if isinstance(entry, dict) else ""
    if not playback_id:
        return []
    return [("Remove from Resume", "RunPlugin(%s)" % addon_url(action="trakt_resume_remove", id=playback_id))]


def resume_art(entry, art):
    art = dict(art or {})
    if entry.get("item_type") != "movie":
        return art or fallback_art("sources")
    image = art.get("landscape") or art.get("fanart") or art.get("thumb") or art.get("poster") or art.get("icon")
    if not image:
        return fallback_landscape_art("movie")
    result = dict(art)
    result.update({
        "thumb": image,
        "thumbnail": image,
        "icon": image,
        "landscape": image,
    })
    result.setdefault("fanart", image)
    return result


def search_catalogs(search_type=""):
    try:
        manifest = get_manifest()
    except RuntimeError as exc:
        error(str(exc))
        return []
    catalogs = []
    for catalog in manifest.get("catalogs", []):
        if search_type and catalog.get("type") != search_type:
            continue
        if any(extra.get("name") == "search" for extra in catalog.get("extra", [])):
            catalogs.append(catalog)
    return catalogs


def search_menu():
    search("")


def settings_menu():
    xbmcplugin.setPluginCategory(HANDLE, "Settings")
    add_action("Configure addon URLs and options", {"action": "configure"}, fallback_art("settings"))
    add_action("Credentials file location", {"action": "credentials_info"}, fallback_art("settings"))
    add_action("Trakt status", {"action": "trakt_status"}, fallback_art("settings"))
    add_action("Authenticate Trakt", {"action": "trakt_auth"}, fallback_art("settings"))
    if trakt.authenticated():
        add_action("Sign out of Trakt", {"action": "trakt_sign_out"}, fallback_art("settings"))
    add_action("Refresh " + metadata_provider_label(), {"action": "refresh_aiometadata"}, fallback_art("settings"))
    add_action("Refresh AIOStreams", {"action": "refresh_aiostreams"}, fallback_art("settings"))
    add_directory("View Mode Setup", {"action": "view_mode_setup"}, fallback_art("settings"))
    add_action("About", {"action": "about"}, fallback_art("default"))
    set_view()


def trakt_status():
    configured = "yes" if trakt.configured() else "no"
    enabled = "yes" if trakt.enabled() else "no"
    authenticated = "yes" if trakt.authenticated() else "no"
    xbmcgui.Dialog().ok(
        "AIOStreams Trakt",
        "Configured: %s\nEnabled: %s\nAuthenticated: %s" % (
            configured,
            enabled,
            authenticated,
        ),
    )
    settings_menu()


def trakt_auth_message(verification_url, user_code):
    return "Go to %s\nCode: %s" % (verification_url, user_code)


def progress_create(progress, heading, message):
    try:
        progress.create(heading, message)
    except TypeError:
        progress.create(heading + "\n" + message)


def progress_update(progress, percent, message):
    try:
        progress.update(percent, message)
    except TypeError:
        progress.update(percent)


def trakt_authenticate():
    if not trakt.configured():
        error("Configure Trakt client ID and secret first")
        settings_menu()
        return
    try:
        code_data = trakt.device_code()
    except trakt.TraktError as exc:
        error("Trakt auth failed: %s" % exc)
        settings_menu()
        return

    device_code = code_data.get("device_code") or ""
    user_code = code_data.get("user_code") or ""
    verification_url = code_data.get("verification_url") or "https://trakt.tv/activate"
    expires_in = safe_int(code_data.get("expires_in"), 600)
    interval = max(1, safe_int(code_data.get("interval"), 5))
    if not device_code or not user_code:
        error("Trakt did not return a device code")
        settings_menu()
        return

    progress = None
    progress_class = getattr(xbmcgui, "DialogProgress", None)
    progress_message = trakt_auth_message(verification_url, user_code)
    if progress_class:
        progress = progress_class()
        progress_create(progress, "AIOStreams Trakt", progress_message)
    else:
        xbmcgui.Dialog().ok(
            "AIOStreams Trakt",
            "Go to:\n%s\n\nEnter code:\n%s\n\nPress OK after approving AIOStreams." % (verification_url, user_code),
        )

    deadline = time.time() + expires_in
    wait_seconds = interval
    try:
        while time.time() < deadline:
            if progress:
                try:
                    if progress.iscanceled():
                        break
                    elapsed = expires_in - int(deadline - time.time())
                    percent = int(max(0, min(100, elapsed * 100 / max(1, expires_in))))
                    progress_update(progress, percent, progress_message)
                except Exception:
                    pass
            try:
                status, token_data = trakt.poll_device_token(device_code)
            except trakt.TraktError as exc:
                error("Trakt auth failed: %s" % exc)
                break
            if status == 200:
                trakt.save_tokens(token_data)
                notify("Trakt authenticated")
                break
            if status == 400:
                pass
            elif status == 429:
                wait_seconds += interval
            elif status == 410:
                error("Trakt code expired; start authentication again")
                break
            elif status == 418:
                error("Trakt authentication was denied")
                break
            else:
                error("Trakt authentication failed: HTTP %s" % status)
                break
            xbmc.sleep(int(wait_seconds * 1000))
    finally:
        if progress:
            try:
                progress.close()
            except Exception:
                pass
    settings_menu()


def trakt_sign_out():
    trakt.revoke()
    notify("Trakt signed out")
    settings_menu()


def list_trakt():
    xbmcplugin.setPluginCategory(HANDLE, "Trakt")
    if not trakt.configured():
        add_action("Configure Trakt", {"action": "settings"}, fallback_art("settings"))
        set_view("videos")
        return
    if not trakt.authenticated():
        add_action("Authenticate Trakt", {"action": "trakt_auth"}, fallback_art("settings"))
        set_view("videos")
        return
    add_directory("Resume", {"action": "trakt_progress", "type": "all"}, fallback_art("sources"))
    add_directory("Next Up", {"action": "trakt_next"}, fallback_art("series"))
    add_directory("Watchlist Movies", {"action": "trakt_watchlist", "type": "movies"}, fallback_art("movie"))
    add_directory("Watchlist Shows", {"action": "trakt_watchlist", "type": "shows"}, fallback_art("series"))
    add_directory("Watched Movies", {"action": "trakt_watched", "type": "movies"}, fallback_art("movie"))
    add_directory("Watched Shows", {"action": "trakt_watched", "type": "shows"}, fallback_art("series"))
    add_directory("Recent Movie History", {"action": "trakt_history", "type": "movies"}, fallback_art("movie"))
    add_directory("Recent Episode History", {"action": "trakt_history", "type": "episodes"}, fallback_art("series"))
    set_view("videos")


def fetch_trakt_items(fetcher, label):
    if not trakt_enabled_for_lists():
        error("Trakt is not authenticated")
        return []
    try:
        data = fetcher()
    except trakt.TraktError as exc:
        error("%s failed: %s" % (label, exc))
        return []
    return data if isinstance(data, list) else []


def trakt_progress_entries(media_type="all"):
    entries = []
    types = ("movies", "episodes") if media_type == "all" else (media_type,)
    for current_type in types:
        entries.extend(fetch_trakt_items(lambda current_type=current_type: trakt.playback(current_type), "Trakt resume"))
    return sorted(entries, key=lambda item: item.get("paused_at") or "", reverse=True)


def add_trakt_progress_entry(entry):
    target = trakt_stream_target(entry)
    if not target:
        return False
    position = trakt_progress_position(entry, target)
    if not should_keep_resume(position, safe_int(target.get("duration"))):
        return False
    label = resume_label({"title": target.get("title")}, position, safe_int(target.get("duration")))
    add_trakt_stream_directory(label, target, position, context_menu=trakt_resume_context_menu(entry))
    return True


def list_trakt_progress(media_type="all"):
    xbmcplugin.setPluginCategory(HANDLE, "Trakt Resume")
    count = 0
    for entry in trakt_progress_entries(media_type):
        if add_trakt_progress_entry(entry):
            count += 1
    if not count:
        add_directory("No Trakt resume items", {"action": "noop"}, fallback_art("sources"), {"title": "No Trakt resume items"})
    set_view("videos", "view_results", cache_to_disc=False)


def list_trakt_next():
    xbmcplugin.setPluginCategory(HANDLE, "Trakt Next Up")
    history_entries = fetch_trakt_items(lambda: trakt.history("episodes", 120), "Trakt next")
    playback_entries = fetch_trakt_items(lambda: trakt.playback("episodes"), "Trakt resume")
    count = 0
    for target in trakt_next_entries(history_entries, playback_entries):
        add_trakt_stream_directory(target.get("title"), target, enrich=False)
        count += 1
    if not count:
        add_directory("No Trakt next up items", {"action": "noop"}, fallback_art("series"), {"title": "No Trakt next up items"})
    set_view("episodes", "view_episodes", cache_to_disc=False)


def list_trakt_watchlist(media_type):
    xbmcplugin.setPluginCategory(HANDLE, "Trakt Watchlist")
    items = fetch_trakt_items(lambda: trakt.watchlist(media_type), "Trakt watchlist")
    count = 0
    for entry in items:
        if entry.get("type") == "show" or (not entry.get("type") and isinstance(entry.get("show"), dict)):
            count += 1 if add_trakt_show_directory(entry) else 0
            continue
        target = trakt_stream_target(entry)
        if target:
            add_trakt_stream_directory(target.get("title"), target)
            count += 1
    if not count:
        add_directory("No Trakt watchlist items", {"action": "noop"}, fallback_art("sources"), {"title": "No Trakt watchlist items"})
    set_view("movies" if media_type == "movies" else "tvshows", "view_results", cache_to_disc=False)


def list_trakt_history(media_type):
    xbmcplugin.setPluginCategory(HANDLE, "Trakt History")
    items = fetch_trakt_items(lambda: trakt.history(media_type), "Trakt history")
    count = 0
    for entry in items:
        target = trakt_stream_target(entry)
        if not target:
            continue
        watched_at = str(entry.get("watched_at") or "")[:10]
        label = target.get("title")
        if watched_at:
            label = "%s (%s)" % (label, watched_at)
        info = dict(target.get("info") or {})
        info["playcount"] = max(1, safe_int(info.get("playcount"), 1))
        target = dict(target, info=info)
        add_trakt_stream_directory(label, target)
        count += 1
    if not count:
        add_directory("No Trakt history items", {"action": "noop"}, fallback_art("sources"), {"title": "No Trakt history items"})
    set_view("episodes" if media_type == "episodes" else "movies", "view_results", cache_to_disc=False)


def list_trakt_watched(media_type):
    xbmcplugin.setPluginCategory(HANDLE, "Trakt Watched")
    items = fetch_trakt_items(lambda: trakt.watched(media_type), "Trakt watched")
    count = 0
    for entry in items:
        plays = max(1, safe_int(entry.get("plays"), 1))
        entry = dict(entry, plays=plays)
        if entry.get("type") == "show" or (not entry.get("type") and isinstance(entry.get("show"), dict)):
            count += 1 if add_trakt_show_directory(entry, plays) else 0
            continue
        target = trakt_stream_target(entry)
        if target:
            add_trakt_stream_directory(target.get("title"), target)
            count += 1
    if not count:
        add_directory("No Trakt watched items", {"action": "noop"}, fallback_art("sources"), {"title": "No Trakt watched items"})
    set_view("movies" if media_type == "movies" else "tvshows", "view_results", cache_to_disc=False)


def list_resume():
    xbmcplugin.setPluginCategory(HANDLE, "Resume")
    entries = [entry for entry in load_resume_entries() if should_keep_resume(safe_int(entry.get("position")), safe_int(entry.get("duration")))]
    count = 0
    stream_contexts = load_stream_contexts()
    for entry in entries:
        position = safe_int(entry.get("position"))
        duration = safe_int(entry.get("duration"))
        label = resume_label(entry, position, duration)
        entry_art = resume_art(entry, art_from_json(entry.get("art")))
        info = {"title": entry.get("title") or label, "plot": entry.get("stream_title") or ""}
        if entry.get("item_type") == "series":
            info["mediatype"] = "episode"
        if duration:
            info["duration"] = duration
        context_menu = resume_context_menu(entry)
        context = {
            "url": entry.get("url") or "",
            "headers": entry.get("headers") or {},
            "resume": position,
            "duration": duration,
            "title": entry.get("title") or "",
            "stream_title": entry.get("stream_title") or "",
            "item_id": entry.get("item_id") or "",
            "item_type": entry.get("item_type") or "",
            "video_id": entry.get("video_id") or "",
            "show_id": entry.get("show_id") or "",
            "show_imdb": entry.get("show_imdb") or "",
            "season": entry.get("season") or "",
            "episode": entry.get("episode") or "",
            "key": entry.get("key") or "",
            "art": entry_art,
        }
        if direct_playback_url(context.get("url")):
            token = register_stream_context(context, stream_contexts, save=False)
            add_playable(label, {
                "action": "play_token",
                "token": token,
                "resume": str(position),
            }, art=entry_art, info=info, context_menu=context_menu)
        else:
            params = resume_sources_params(context, position, duration, entry_art)
            if params:
                add_directory(label, params, entry_art, info, context_menu=context_menu)
            else:
                add_directory(label, {"action": "noop"}, entry_art, info, context_menu=context_menu)
        count += 1
    if entries:
        save_stream_contexts(stream_contexts)
    if trakt_enabled_for_lists():
        for entry in trakt_progress_entries("all"):
            if add_trakt_progress_entry(entry):
                count += 1
    if not count:
        add_directory("No resumable items", {"action": "noop"}, fallback_art("sources"), {"title": "No resumable items"})
    set_view("episodes" if count else "videos", "view_episodes", cache_to_disc=False)


def mark_resume_watched(key):
    if remove_resume_entry(key):
        xbmc.executebuiltin("Container.Refresh")


def remove_trakt_resume(playback_id):
    if not playback_id:
        return
    try:
        if trakt.remove_playback(playback_id):
            xbmc.executebuiltin("Container.Refresh")
    except trakt.TraktError as exc:
        error("Trakt resume remove failed: %s" % exc)


def resume_label(entry, position, duration):
    title = entry.get("title") or entry.get("stream_title") or "Untitled"
    if duration:
        return "%s (%s / %s)" % (title, format_hms(position), format_hms(duration))
    return "%s (%s)" % (title, format_hms(position))


def format_hms(seconds):
    seconds = max(0, safe_int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours:
        return "%dh%02dm" % (hours, minutes)
    return "%dm" % minutes


def search(search_type="", query="", resume=0, duration=0, season="", episode="", resume_key=""):
    query = query.strip()
    if not query:
        query = xbmcgui.Dialog().input("Search " + metadata_provider_label(), type=xbmcgui.INPUT_ALPHANUM).strip()
        if query:
            search(search_type, query, resume, duration, season, episode, resume_key)
        else:
            set_view("movies", "view_search", fallback_view_setting="view_results")
        return

    xbmcplugin.setPluginCategory(HANDLE, "Search: " + query)
    cached = cached_search_results(search_type, query)
    if cached is not None:
        for result in cached:
            meta = result.get("meta") if isinstance(result, dict) else {}
            item_type = result.get("type") if isinstance(result, dict) else ""
            item_id = result.get("id") if isinstance(result, dict) else ""
            if meta and item_type and item_id:
                add_video_item(meta, item_type, item_id, search_result_art(meta, item_type), resume=resume, duration=duration, season=season, episode=episode, resume_key=resume_key)
        set_view("movies", "view_search", fallback_view_setting="view_results")
        return

    seen = set()
    results = []
    errors = []
    searched_catalogs = 0
    result_limit = max(1, min(setting_int("search_result_limit", 10), 100))
    for catalog in search_catalogs(search_type):
        searched_catalogs += 1
        extra_path = catalog_extra(catalog, "", 0, query)
        url = join_url(aiometa_base(), "catalog", catalog.get("type"), catalog.get("id"))
        url += "/" + extra_path + ".json"
        try:
            response = get_json(service_url(url, "aiometadata"))
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        batch = []
        for meta in response.get("metas", []):
            item_id = meta.get("id")
            item_type = meta.get("type") or catalog.get("type")
            if not item_id:
                continue
            key = "%s:%s" % (item_type, item_id)
            if key in seen:
                continue
            seen.add(key)
            batch.append((item_type, item_id, meta))
            if len(results) + len(batch) >= result_limit:
                break
        enriched_batch = parallel_map(lambda entry: enrich_meta(entry[0], entry[2]), batch)
        for (item_type, item_id, _meta), enriched in zip(batch, enriched_batch):
            results.append({"type": item_type, "id": item_id, "meta": enriched})
            add_video_item(enriched, item_type, item_id, search_result_art(enriched, item_type), resume=resume, duration=duration, season=season, episode=episode, resume_key=resume_key)
        if len(results) >= result_limit:
            break
    if results:
        save_search_results(search_type, query, results)
    elif errors:
        xbmc.log("AIOStreams search failed for %d catalog(s): %s" % (searched_catalogs, " | ".join(errors[:3])), xbmc.LOGWARNING)
        error("Search failed: %s" % errors[0])
    elif not searched_catalogs:
        notify("No searchable catalogs found")
        add_directory("No searchable catalogs found", {"action": "noop"}, fallback_art("search"), {"title": "No searchable catalogs found"})
    else:
        add_directory("No search results", {"action": "noop"}, fallback_art("search"), {"title": "No search results"})
    set_view("movies", "view_search", fallback_view_setting="view_results")


def view_mode_setup():
    views.view_mode_setup(add_directory, fallback_art)


def refresh_service(service):
    if service == "aiostreams":
        base = aiostreams_base()
        label = "AIOStreams"
    else:
        base = aiometa_base()
        label = metadata_provider_label()
    if not base:
        error("%s URL is not configured" % label)
        set_view()
        return
    token = str(int(time.time()))
    ADDON.setSetting(refresh_setting(service), token)
    try:
        manifest = get_json(service_url(base + "/manifest.json", service))
    except RuntimeError as exc:
        error("%s refresh failed: %s" % (label, exc))
        set_view()
        return
    name = manifest.get("name") or label
    clear_search_cache()
    clear_api_cache()
    notify("%s refreshed" % name)
    xbmc.executebuiltin("Container.Update(%s,replace)" % PLUGIN_URL)
    set_view()


def view_mode_candidates(setting_id):
    views.view_mode_candidates(setting_id, add_directory, fallback_art, error)


def view_mode_custom(setting_id, content):
    views.view_mode_custom(setting_id, content, view_mode_candidates, view_mode_test)


def view_mode_test(setting_id, view_id, content):
    views.view_mode_test(setting_id, view_id, content, add_directory, notify, error, view_mode_setup)


def about():
    xbmcgui.Dialog().ok(
        "AIOStreams for Kodi",
        "Configure the AIOStreams manifest URL for source lookup and playback. AIOMetadata is optional for catalogs, search, and details; Cinemeta is used when AIOMetadata is not configured.",
    )
    list_root()


def credentials_info():
    path = credentials_path()
    data, status = read_credentials_status()
    aios_setting = endpoint_base("aiostreams_url")
    aios_credentials = endpoint_base_value(credential_value_from(data, AIOS_CREDENTIAL_KEYS))
    aiometa_setting = endpoint_base("aiometadata_url")
    aiometa_credentials = endpoint_base_value(credential_value_from(data, AIOMETA_CREDENTIAL_KEYS))
    aios_source = "Kodi setting" if aios_setting else ("credentials file" if aios_credentials else "not configured")
    aiometa_source = "Kodi setting" if aiometa_setting else ("credentials file" if aiometa_credentials else "Cinemeta fallback")
    trakt_client_source = "Kodi setting" if setting("trakt_client_id") else ("credentials file" if credential_value_from(data, ("trakt_client_id", "trakt_api_key", "trakt_client")) else "not configured")
    trakt_enabled_source = "credentials file" if credential_bool_from(data, "trakt_enabled", False) else ("Kodi setting" if setting_bool("trakt_enabled", False) else "disabled")
    xbmcgui.Dialog().ok(
        "AIOStreams Credentials",
        "Edit this JSON file to configure manifest URLs and optional Trakt API credentials:\n\n%s\n\nStatus: %s\nAIOStreams: %s\nMetadata: %s\nTrakt enabled: %s\nTrakt client: %s\n\nKeys:\naiostreams_url\naiometadata_url (optional; Cinemeta is used when blank)\ntrakt_enabled\ntrakt_scrobble\ntrakt_client_id\ntrakt_client_secret\ntrakt_redirect_uri" % (
            path,
            status,
            aios_source,
            aiometa_source,
            trakt_enabled_source,
            trakt_client_source,
        ),
    )
    settings_menu()


def safe_int(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def runtime_seconds(value):
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value) * 60
    text = str(value).strip().lower()
    hours = re.search(r"(\d+)\s*h", text)
    minutes = re.search(r"(\d+)\s*m", text)
    if hours or minutes:
        return (safe_int(hours.group(1)) * 3600 if hours else 0) + (safe_int(minutes.group(1)) * 60 if minutes else 0)
    number = re.search(r"\d+", text)
    return safe_int(number.group(0)) * 60 if number else 0


def route():
    try:
        dispatch()
    finally:
        flush_api_cache()


def dispatch():
    params = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
    action = params.get("action", "root")
    if action == "settings":
        settings_menu()
    elif action == "configure":
        try:
            ADDON.openSettings()
        except Exception as exc:
            xbmcgui.Dialog().ok("AIOStreams", "Kodi could not open addon settings. Using built-in test URLs.\n\n%s" % exc)
        settings_menu()
    elif action == "credentials_info":
        credentials_info()
    elif action == "trakt_status":
        trakt_status()
    elif action == "trakt_auth":
        trakt_authenticate()
    elif action == "trakt_sign_out":
        trakt_sign_out()
    elif action == "trakt":
        list_trakt()
    elif action == "trakt_progress":
        list_trakt_progress(params.get("type", "all"))
    elif action == "trakt_resume_remove":
        remove_trakt_resume(params.get("id", ""))
    elif action == "trakt_next":
        list_trakt_next()
    elif action == "trakt_watchlist":
        list_trakt_watchlist(params.get("type", "movies"))
    elif action == "trakt_history":
        list_trakt_history(params.get("type", "movies"))
    elif action == "trakt_watched":
        list_trakt_watched(params.get("type", "movies"))
    elif action == "refresh_aiometadata":
        refresh_service("aiometadata")
    elif action == "refresh_aiostreams":
        refresh_service("aiostreams")
    elif action == "imdb_refresh_show":
        refresh_imdb_show_scores(params.get("type", "series"), params.get("id", ""), params.get("quiet") == "1")
    elif action == "imdb_refresh_season":
        refresh_imdb_season_worker(params.get("type", "series"), params.get("id", ""), params.get("season", "1"))
    elif action == "about":
        about()
    elif action == "catalog":
        list_catalog(params.get("type", "movie"), params.get("id", ""), params.get("genre", ""), safe_int(params.get("skip", "0")))
    elif action == "details":
        list_details(
            params.get("type", "movie"),
            params.get("id", ""),
            safe_int(params.get("resume")),
            safe_int(params.get("duration")),
            params.get("season", ""),
            params.get("episode", ""),
            params.get("resume_key", ""),
        )
    elif action == "episodes":
        list_episodes(
            params.get("type", "series"),
            params.get("id", ""),
            params.get("season", "1"),
            safe_int(params.get("resume")),
            safe_int(params.get("duration")),
            params.get("episode", ""),
            params.get("resume_key", ""),
        )
    elif action == "streams":
        list_streams(
            params.get("type", "movie"),
            params.get("id", ""),
            params.get("title", ""),
            params.get("art", ""),
            params.get("video_id", ""),
            params.get("show_id", ""),
            params.get("show_imdb", ""),
            params.get("season", ""),
            params.get("episode", ""),
            safe_int(params.get("resume")),
            safe_int(params.get("duration")),
            params.get("resume_key", ""),
        )
    elif action == "play":
        play(params.get("url", ""), params.get("headers", "{}"), 0, {
            "title": params.get("title", ""),
            "stream_title": params.get("stream_title", ""),
            "item_id": params.get("item_id", ""),
            "item_type": params.get("item_type", ""),
            "video_id": params.get("video_id", ""),
            "show_id": params.get("show_id", ""),
            "show_imdb": params.get("show_imdb", ""),
            "season": params.get("season", ""),
            "episode": params.get("episode", ""),
            "resume": safe_int(params.get("resume")),
            "duration": safe_int(params.get("duration")),
            "art": art_from_json(params.get("art", "")),
        })
    elif action == "play_token":
        play_token(params.get("token", ""), safe_int(params.get("resume")))
    elif action == "resume":
        list_resume()
    elif action == "resume_mark_watched":
        mark_resume_watched(params.get("key", ""))
    elif action == "resume_play":
        play(params.get("url", ""), params.get("headers", "{}"), safe_int(params.get("resume")), {
            "key": params.get("key", ""),
            "title": params.get("title", ""),
            "stream_title": params.get("stream_title", ""),
            "item_id": params.get("item_id", ""),
            "item_type": params.get("item_type", ""),
            "video_id": params.get("video_id", ""),
            "show_id": params.get("show_id", ""),
            "show_imdb": params.get("show_imdb", ""),
            "season": params.get("season", ""),
            "episode": params.get("episode", ""),
            "duration": safe_int(params.get("duration")),
            "art": art_from_json(params.get("art", "")),
        })
    elif action == "search_menu":
        search_menu()
    elif action == "search":
        search(
            params.get("type", ""),
            params.get("query", ""),
            safe_int(params.get("resume")),
            safe_int(params.get("duration")),
            params.get("season", ""),
            params.get("episode", ""),
            params.get("resume_key", ""),
        )
    elif action == "view_mode_setup":
        view_mode_setup()
    elif action == "view_mode_candidates":
        view_mode_candidates(params.get("setting", ""))
    elif action == "view_mode_custom":
        view_mode_custom(params.get("setting", ""), params.get("content", "videos"))
    elif action == "view_mode_test":
        view_mode_test(params.get("setting", ""), params.get("view", "0"), params.get("content", "videos"))
    elif action == "noop":
        set_view()
    else:
        list_root()


if __name__ == "__main__":
    route()
