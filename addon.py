import base64
import html
import hashlib
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib import views


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
CREDENTIALS_FILE = "credentials.json"
IMDB_GRAPHQL_URL = "https://api.graphql.imdb.com/"
IMDB_SCORE_REFRESH_RECENT = 24 * 3600
IMDB_SCORE_REFRESH_CURRENT = 3 * 24 * 3600
IMDB_SCORE_REFRESH_OLD = 30 * 24 * 3600
SEARCH_CACHE_TTL = 30 * 60
META_CACHE_TTL = 30 * 60
PLAYBACK_HEADER_DENYLIST = {"range", "if-range", "content-range", "content-length"}

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


def save_credentials_template(path):
    data = {
        "aiostreams_url": "",
        "aiometadata_url": "",
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
    entries = sorted(entries, key=lambda item: item.get("updated", 0), reverse=True)[:50]
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"entries": entries}, handle, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams could not save resume history: %s" % exc, xbmc.LOGWARNING)


def resume_key(url, item_id="", stream_title=""):
    value = "%s|%s|%s" % (item_id, stream_title, url)
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def upsert_resume_entry(entry):
    entries = [item for item in load_resume_entries() if item.get("key") != entry.get("key")]
    entry["updated"] = int(time.time())
    entries.insert(0, entry)
    save_resume_entries(entries)


def remove_resume_entry(key):
    save_resume_entries([item for item in load_resume_entries() if item.get("key") != key])


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
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"entries": dict(sorted_entries)}, handle, indent=2, sort_keys=True)
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
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"entries": kept}, handle, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams could not save API cache: %s" % exc, xbmc.LOGWARNING)


def clear_api_cache():
    save_api_cache({"entries": {}})


def cached_service_json(url, service, ttl):
    final_url = service_url(url, service)
    key = hashlib.sha1(final_url.encode("utf-8")).hexdigest()
    cache = load_api_cache()
    entry = cache.get("entries", {}).get(key, {})
    if entry and int(time.time()) - safe_int(entry.get("updated")) <= ttl and isinstance(entry.get("data"), dict):
        return entry.get("data")
    data = get_json(final_url)
    cache.setdefault("entries", {})[key] = {"updated": int(time.time()), "ttl": ttl, "data": data}
    save_api_cache(cache)
    return data


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
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(context, handle, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams could not save current playback context: %s" % exc, xbmc.LOGWARNING)


def load_current_playback():
    path = profile_path(CURRENT_PLAYBACK_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump({"tokens": tokens}, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
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
    return endpoint_base_value(load_credentials().get(key, ""))


def endpoint_base_value(value):
    value = str(value or "").strip().rstrip("/")
    if not value:
        return ""
    if value.endswith("/manifest.json"):
        return value[: -len("/manifest.json")]
    return value


def join_url(base, *parts):
    path = "/".join(urllib.parse.quote(str(part), safe="") for part in parts if part != "")
    return base.rstrip("/") + "/" + path


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=setting_int("request_timeout", 25)) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("HTTP %s from %s" % (exc.code, url))
    except urllib.error.URLError as exc:
        raise RuntimeError("Network error: %s" % exc.reason)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Invalid JSON from %s: %s" % (url, exc))


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
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
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


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_playback_redirect(url, headers=None):
    request_headers = sanitize_playback_headers(headers)
    if not any(str(key).lower() == "user-agent" for key in request_headers):
        request_headers["User-Agent"] = USER_AGENT
    request = urllib.request.Request(url, headers=request_headers)
    opener = urllib.request.build_opener(NoRedirectHandler)
    try:
        opener.open(request, timeout=setting_int("request_timeout", 25)).close()
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            location = exc.headers.get("Location")
            if location:
                return urllib.parse.urljoin(url, location)
        raise RuntimeError("HTTP %s from %s" % (exc.code, url))
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
    return get_json(service_url(base + "/manifest.json", "aiometadata"))


def find_catalog(catalog_type, catalog_id):
    for catalog in get_manifest().get("catalogs", []):
        if catalog.get("type") == catalog_type and catalog.get("id") == catalog_id:
            return catalog
    return {}


def add_directory(label, params, art=None, info=None):
    item = xbmcgui.ListItem(label=label)
    set_item_art(item, art)
    if info:
        set_item_info(item, info)
    xbmcplugin.addDirectoryItem(HANDLE, addon_url(**params), item, True)


def add_action(label, params, art=None, info=None):
    item = xbmcgui.ListItem(label=label)
    set_item_art(item, art)
    if info:
        set_item_info(item, info)
    xbmcplugin.addDirectoryItem(HANDLE, addon_url(**params), item, False)


def add_playable(label, params, art=None, info=None):
    item = xbmcgui.ListItem(label=label)
    item.setProperty("IsPlayable", "true")
    set_item_art(item, art)
    if info:
        set_item_info(item, info)
    xbmcplugin.addDirectoryItem(HANDLE, addon_url(**params), item, False)


def set_item_info(item, info):
    video_info = dict(info)
    video_info.pop("imdbVotes", None)
    item.setInfo("video", video_info)
    rating = display_rating(info.get("imdbRating") or info.get("rating"))
    if not rating:
        return
    try:
        votes = safe_int(info.get("imdbVotes") or info.get("votes"))
        item.setRating("imdb", float(rating), votes, True)
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


def add_video_item(meta, item_type, item_id, art=None):
    label = meta.get("name") or meta.get("title") or item_id
    item_art = art or meta_art(meta) or fallback_art(item_type_art_key(item_type))
    params = {"action": "details", "type": item_type, "id": item_id}
    if item_type == "movie":
        params = {"action": "streams", "type": item_type, "id": best_stream_id(meta, item_id), "title": label, "art": art_json(item_art)}
    add_directory(label, params, item_art, meta_info(meta))


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
            break
        metas.extend(page_metas)
        current_skip += len(page_metas)

    for meta in metas:
        item_type = meta.get("type") or catalog_type
        item_id = meta.get("id")
        if not item_id:
            continue
        add_video_item(enrich_meta(item_type, meta), item_type, item_id)

    if metas and not search_query:
        add_directory("Next page", {
            "action": "catalog",
            "type": catalog_type,
            "id": catalog_id,
            "genre": genre,
            "skip": str(current_skip),
        }, catalog_art(catalog))
    set_view("movies", "view_results")


def list_details(item_type, item_id):
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
            }, season_art(meta, season, tvmaze_seasons.get(str(season))), season_info(meta, season))
    else:
        add_directory("Sources", {
            "action": "streams",
            "type": item_type,
            "id": best_stream_id(meta, item_id),
            "title": title,
            "art": art_json(meta_art(meta)),
        }, meta_art(meta), meta_info(meta))

    set_view("seasons" if videos else "movies", "view_seasons" if videos else "view_results")


def list_episodes(item_type, item_id, season):
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
    imdb_scores = load_imdb_episode_scores() if imdb_episode_scores_enabled() and imdb_id else {"shows": {}}
    if imdb_episode_scores_enabled() and imdb_id and imdb_cache_stale(imdb_scores, imdb_id, season, videos):
        refresh_imdb_season_scores(imdb_id, season)
        imdb_scores = load_imdb_episode_scores()
    tvmaze_fallbacks = {}
    if item_type == "series" and any(episode_needs_fallback(video) for video in videos):
        tvmaze_fallbacks = tvmaze_episode_fallbacks(meta, item_id, season)

    for video in videos:
        video = merge_episode_fallback(video, tvmaze_fallbacks.get(video_episode(video) or ""))
        video = enrich_episode_meta(item_type, video)
        video = apply_imdb_episode_score(video, imdb_scores, imdb_id, season)
        video_id = video.get("id")
        if not video_id:
            continue
        episode = video_episode(video) or ""
        episode_title = video.get("title") or video.get("name") or "Episode %s" % episode
        label = episode_title
        if episode and not str(label).lower().startswith("episode"):
            label = "%s. %s" % (episode, label)
        label = episode_label(label, video)
        show_title = meta.get("name") or meta.get("title") or item_id
        content_title = resume_episode_title(show_title, season, episode, episode_title)
        info = episode_info(video, meta, season, episode)
        if str(episode).isdigit():
            info["episode"] = int(episode)
        if str(season).isdigit():
            info["season"] = int(season)
        item_art = episode_art(meta, video)
        add_directory(label, {
            "action": "streams",
            "type": item_type,
            "id": best_stream_id(video, video_id),
            "title": content_title,
            "art": art_json(item_art),
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


def episode_label(label, video):
    return label


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


def list_streams(item_type, item_id, content_title="", content_art_json=""):
    base = aiostreams_base()
    if not base:
        error("AIOStreams manifest URL is not configured")
        set_view()
        return
    url = join_url(base, "stream", item_type, item_id) + ".json"
    try:
        response = get_json(service_url(url, "aiostreams"))
    except RuntimeError as exc:
        error(str(exc))
        set_view()
        return

    xbmcplugin.setPluginCategory(HANDLE, "Sources")
    streams = response.get("streams", [])
    content_art = art_from_json(content_art_json)
    playable_count = 0
    for index, stream in enumerate(streams, start=1):
        stream_url = stream.get("url") or stream.get("externalUrl")
        label = stream_label(stream, index)
        info = {"title": label, "plot": stream_plot(stream)}
        if stream_url and stream_url.startswith(("http://", "https://")):
            playable_count += 1
            add_playable(label, {
                "action": "play",
                "url": stream_url,
                "headers": json.dumps(stream_headers(stream)),
                "item_id": item_id,
                "item_type": item_type,
                "title": content_title or item_id,
                "stream_title": label,
                "art": art_json(content_art),
            }, art=fallback_art("sources"), info=info)
        else:
            add_directory("Unsupported: " + label, {"action": "noop"}, art=fallback_art("sources"), info=info)

    if not playable_count:
        notify("No direct playable HTTP streams returned")
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

    playback_headers.setdefault("Cache-Control", "no-cache")
    playback_headers.setdefault("Pragma", "no-cache")
    playback_headers.setdefault("Connection", "close")
    return resolved_url, playback_headers


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
        xbmc.log("AIOStreams playback resolve failed for %s: %s" % (url, exc), xbmc.LOGERROR)
        error(str(exc))
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    playback_url, playback_headers = playback_url_and_headers(url, resolved_url, headers)
    resolved_host = urllib.parse.urlparse(playback_url).netloc
    mime = playback_mime(playback_url, context.get("stream_title") or context.get("title") or "")
    xbmc.log("AIOStreams resolved playback host: %s, headers: %s, mime: %s" % (
        resolved_host or "unknown",
        ",".join(sorted(playback_headers.keys())) or "none",
        mime or "auto",
    ), xbmc.LOGINFO)
    if setting_bool("use_local_forwarder", True):
        try:
            item_path = register_forwarder_url(playback_url, playback_headers, context)
            xbmc.log("AIOStreams playback engine: local-forwarder", xbmc.LOGINFO)
        except Exception as exc:
            xbmc.log("AIOStreams local forwarder registration failed, using direct playback: %s" % exc, xbmc.LOGWARNING)
            item_path = kodi_header_url(playback_url, playback_headers)
            xbmc.log("AIOStreams playback engine: kodi-builtin", xbmc.LOGINFO)
    else:
        item_path = kodi_header_url(playback_url, playback_headers)
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
        item.setInfo("video", {"title": context.get("title")})
    if context:
        save_current_playback(dict(context, url=url, headers=headers, resume=resume_seconds))
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def should_keep_resume(position, duration):
    if position < 60:
        return False
    if duration and duration > 0 and position >= duration * 0.9:
        return False
    return True


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
    add_action("Refresh " + metadata_provider_label(), {"action": "refresh_aiometadata"}, fallback_art("settings"))
    add_action("Refresh AIOStreams", {"action": "refresh_aiostreams"}, fallback_art("settings"))
    add_directory("View Mode Setup", {"action": "view_mode_setup"}, fallback_art("settings"))
    add_action("About", {"action": "about"}, fallback_art("default"))
    set_view()


def list_resume():
    xbmcplugin.setPluginCategory(HANDLE, "Resume")
    entries = [entry for entry in load_resume_entries() if should_keep_resume(safe_int(entry.get("position")), safe_int(entry.get("duration")))]
    if not entries:
        add_directory("No resumable items", {"action": "noop"}, fallback_art("sources"), {"title": "No resumable items"})
        set_view("videos")
        return
    for entry in entries:
        position = safe_int(entry.get("position"))
        duration = safe_int(entry.get("duration"))
        label = resume_label(entry, position, duration)
        entry_art = art_from_json(entry.get("art")) or fallback_art("sources")
        info = {"title": entry.get("title") or label, "plot": entry.get("stream_title") or ""}
        if entry.get("item_type") == "series":
            info["mediatype"] = "episode"
        if duration:
            info["duration"] = duration
        add_playable(label, {
            "action": "resume_play",
            "url": entry.get("url") or "",
            "headers": json.dumps(entry.get("headers") or {}),
            "resume": str(position),
            "duration": str(duration),
            "title": entry.get("title") or "",
            "stream_title": entry.get("stream_title") or "",
            "item_id": entry.get("item_id") or "",
            "item_type": entry.get("item_type") or "",
            "key": entry.get("key") or "",
            "art": art_json(entry_art),
        }, art=entry_art, info=info)
    set_view("episodes", "view_episodes", cache_to_disc=False)


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


def search(search_type="", query=""):
    query = query.strip()
    if not query:
        query = xbmcgui.Dialog().input("Search " + metadata_provider_label(), type=xbmcgui.INPUT_ALPHANUM).strip()
        if query:
            search(search_type, query)
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
                add_video_item(meta, item_type, item_id, search_result_art(meta, item_type))
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
        for meta in response.get("metas", []):
            item_id = meta.get("id")
            item_type = meta.get("type") or catalog.get("type")
            if not item_id:
                continue
            key = "%s:%s" % (item_type, item_id)
            if key in seen:
                continue
            seen.add(key)
            enriched = enrich_meta(item_type, meta)
            results.append({"type": item_type, "id": item_id, "meta": enriched})
            add_video_item(enriched, item_type, item_id, search_result_art(enriched, item_type))
            if len(results) >= result_limit:
                break
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
    load_credentials()
    xbmcgui.Dialog().ok(
        "AIOStreams Credentials",
        "Edit this JSON file to configure manifest URLs:\n\n%s\n\nKeys:\naiostreams_url\naiometadata_url (optional; Cinemeta is used when blank)" % path,
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
    elif action == "refresh_aiometadata":
        refresh_service("aiometadata")
    elif action == "refresh_aiostreams":
        refresh_service("aiostreams")
    elif action == "imdb_refresh_show":
        refresh_imdb_show_scores(params.get("type", "series"), params.get("id", ""), params.get("quiet") == "1")
    elif action == "about":
        about()
    elif action == "catalog":
        list_catalog(params.get("type", "movie"), params.get("id", ""), params.get("genre", ""), safe_int(params.get("skip", "0")))
    elif action == "details":
        list_details(params.get("type", "movie"), params.get("id", ""))
    elif action == "episodes":
        list_episodes(params.get("type", "series"), params.get("id", ""), params.get("season", "1"))
    elif action == "streams":
        list_streams(params.get("type", "movie"), params.get("id", ""), params.get("title", ""), params.get("art", ""))
    elif action == "play":
        play(params.get("url", ""), params.get("headers", "{}"), 0, {
            "title": params.get("title", ""),
            "stream_title": params.get("stream_title", ""),
            "item_id": params.get("item_id", ""),
            "item_type": params.get("item_type", ""),
            "duration": safe_int(params.get("duration")),
            "art": art_from_json(params.get("art", "")),
        })
    elif action == "resume":
        list_resume()
    elif action == "resume_play":
        play(params.get("url", ""), params.get("headers", "{}"), safe_int(params.get("resume")), {
            "key": params.get("key", ""),
            "title": params.get("title", ""),
            "stream_title": params.get("stream_title", ""),
            "item_id": params.get("item_id", ""),
            "item_type": params.get("item_type", ""),
            "duration": safe_int(params.get("duration")),
            "art": art_from_json(params.get("art", "")),
        })
    elif action == "search_menu":
        search_menu()
    elif action == "search":
        search(params.get("type", ""), params.get("query", ""))
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
