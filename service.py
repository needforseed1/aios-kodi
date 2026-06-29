import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib

import xbmc
import xbmcaddon
import xbmcvfs


ADDON = xbmcaddon.Addon("plugin.video.aiostreams")
RESUME_FILE = "resume.json"
CURRENT_PLAYBACK_FILE = "current_playback.json"
FORWARDER_TOKENS_FILE = "forwarder_tokens.json"
USER_AGENT = "AIOStreams/0.1 Kodi"
PLAYBACK_HEADER_DENYLIST = {"range", "if-range", "content-range", "content-length"}
CURRENT_PLAYBACK_TTL = 10 * 60


class ForwarderServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def profile_path(filename):
    profile = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    if not os.path.isdir(profile):
        os.makedirs(profile, exist_ok=True)
    return os.path.join(profile, filename)


def setting_int(setting_id, default):
    try:
        return int((ADDON.getSetting(setting_id) or "").strip() or default)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def load_json(filename, default):
    path = profile_path(filename)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def load_resume_entries():
    data = load_json(RESUME_FILE, {"entries": []})
    entries = data.get("entries") if isinstance(data, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def save_resume_entries(entries):
    path = profile_path(RESUME_FILE)
    entries = sorted(entries, key=lambda item: item.get("updated", 0), reverse=True)[:50]
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"entries": entries}, handle, indent=2, sort_keys=True)
    except OSError as exc:
        xbmc.log("AIOStreams service could not save resume history: %s" % exc, xbmc.LOGWARNING)


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


def should_keep_resume(position, duration):
    if position < 60:
        return False
    if duration and duration > 0 and position >= duration * 0.9:
        return False
    return True


def load_current_playback():
    data = load_json(CURRENT_PLAYBACK_FILE, {})
    return data if isinstance(data, dict) else {}


def clear_current_playback():
    path = profile_path(CURRENT_PLAYBACK_FILE)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({}, handle)
    except OSError as exc:
        xbmc.log("AIOStreams service could not clear current playback: %s" % exc, xbmc.LOGWARNING)


def fresh_context(context):
    if not context:
        return False
    started = safe_int(context.get("started"))
    if not started:
        return False
    return time.time() - started <= CURRENT_PLAYBACK_TTL


def entry_from_context(context):
    key = context.get("key") or resume_key(context.get("url", ""), context.get("item_id", ""), context.get("stream_title", ""))
    return {
        "key": key,
        "title": context.get("title") or context.get("stream_title") or "Untitled",
        "stream_title": context.get("stream_title") or "",
        "item_id": context.get("item_id") or "",
        "item_type": context.get("item_type") or "",
        "url": context.get("url") or "",
        "headers": context.get("headers") or {},
        "art": context.get("art") if isinstance(context.get("art"), dict) else {},
        "position": safe_int(context.get("resume")),
        "duration": 0,
    }


def load_tokens():
    path = profile_path(FORWARDER_TOKENS_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    tokens = data.get("tokens") if isinstance(data, dict) else {}
    return tokens if isinstance(tokens, dict) else {}


def sanitize_playback_headers(headers):
    if not isinstance(headers, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in headers.items()
        if key and value is not None and str(key).lower() not in PLAYBACK_HEADER_DENYLIST
    }


def token_from_path(path):
    parts = urllib.parse.urlparse(path)
    pieces = [urllib.parse.unquote(piece) for piece in parts.path.split("/") if piece]
    if len(pieces) >= 2 and pieces[0] == "play":
        return pieces[1]
    return ""


def copy_headers(handler, response):
    for key in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag"):
        value = response.headers.get(key)
        if value:
            handler.send_header(key, value)
    handler.send_header("Connection", "close")


def maybe_seek_resume(player, state, position, duration):
    resume_at = safe_int(state.get("resume_at"))
    if resume_at <= 30 or state.get("resume_seek_done"):
        return False
    if position >= resume_at - 5:
        state["resume_seek_done"] = True
        xbmc.log("AIOStreams resume seek confirmed at %ss" % position, xbmc.LOGINFO)
        return False

    now = time.time()
    started = state.get("resume_seek_started") or now
    state["resume_seek_started"] = started
    if now - started > 45:
        state["resume_seek_done"] = True
        xbmc.log("AIOStreams resume seek gave up after 45s: target=%ss position=%ss duration=%ss" % (resume_at, position, duration), xbmc.LOGWARNING)
        return False
    if now - float(state.get("resume_last_seek") or 0) < 0.3:
        return True

    state["resume_last_seek"] = now
    state["resume_seek_attempts"] = safe_int(state.get("resume_seek_attempts")) + 1
    try:
        player.seekTime(float(resume_at))
        xbmc.log("AIOStreams resume seek requested: %ss attempt=%s position=%ss duration=%ss" % (
            resume_at,
            state.get("resume_seek_attempts"),
            position,
            duration,
        ), xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log("AIOStreams resume seek failed: %s" % exc, xbmc.LOGWARNING)
    return True


def record_resume(player, state):
    if player.isPlaying():
        context = load_current_playback()
        if fresh_context(context):
            next_entry = entry_from_context(context)
            if next_entry.get("key") and next_entry.get("key") != state.get("active_key"):
                state["active_key"] = next_entry.get("key")
                state["entry"] = next_entry
                state["last_save"] = 0
                state["resume_at"] = safe_int(context.get("resume"))
                state["resume_seek_done"] = False
                state["resume_seek_started"] = 0
                state["resume_last_seek"] = 0
                state["resume_seek_attempts"] = 0
                state["last_position"] = 0
                xbmc.log("AIOStreams resume tracking started: %s" % next_entry.get("title"), xbmc.LOGINFO)

        entry = state.get("entry")
        if not entry:
            return
        try:
            position = int(player.getTime())
            duration = int(player.getTotalTime())
        except Exception:
            return
        if maybe_seek_resume(player, state, position, duration):
            return
        if position <= 0:
            return
        entry["position"] = position
        entry["duration"] = duration
        now = time.time()
        last_position = safe_int(state.get("last_position"))
        position_jump = last_position and abs(position - last_position) >= 30
        state["last_position"] = position
        if position_jump or now - float(state.get("last_save") or 0) >= 2:
            if should_keep_resume(position, duration):
                upsert_resume_entry(entry)
                xbmc.log("AIOStreams resume saved at %ss/%ss" % (position, duration), xbmc.LOGDEBUG)
            state["last_save"] = now
        return

    active_key = state.get("active_key")
    entry = state.get("entry")
    if active_key and entry:
        position = safe_int(entry.get("position"))
        duration = safe_int(entry.get("duration"))
        if should_keep_resume(position, duration):
            upsert_resume_entry(entry)
            xbmc.log("AIOStreams resume finalized at %ss/%ss" % (position, duration), xbmc.LOGINFO)
        else:
            remove_resume_entry(active_key)
        clear_current_playback()
    state["active_key"] = ""
    state["entry"] = None
    state["last_save"] = 0
    state["resume_at"] = 0
    state["resume_seek_done"] = False
    state["resume_seek_started"] = 0
    state["resume_last_seek"] = 0
    state["resume_seek_attempts"] = 0
    state["last_position"] = 0


class ForwarderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        xbmc.log("AIOStreams forwarder: " + (fmt % args), xbmc.LOGDEBUG)

    def do_HEAD(self):
        self.handle_forward(head_only=True)

    def do_GET(self):
        self.handle_forward(head_only=False)

    def handle_forward(self, head_only=False):
        token = token_from_path(self.path)
        entry = load_tokens().get(token)
        if not isinstance(entry, dict) or not entry.get("url"):
            self.send_error(404, "Playback token not found")
            return

        headers = sanitize_playback_headers(entry.get("headers") or {})
        headers.setdefault("User-Agent", USER_AGENT)
        if self.headers.get("Range"):
            headers["Range"] = self.headers.get("Range")
        if self.headers.get("If-Range"):
            headers["If-Range"] = self.headers.get("If-Range")

        request = urllib.request.Request(entry.get("url"), headers=headers, method="HEAD" if head_only else "GET")
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as exc:
            if head_only and exc.code in (405, 501):
                self.fallback_head(entry, headers)
                return
            self.send_response(exc.code)
            for key in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                value = exc.headers.get(key)
                if value:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            return
        except urllib.error.URLError as exc:
            xbmc.log("AIOStreams forwarder upstream error: %s" % exc, xbmc.LOGWARNING)
            self.send_error(502, "Upstream playback error")
            return

        with response:
            self.send_response(response.getcode())
            copy_headers(self, response)
            self.end_headers()
            if not head_only:
                self.copy_body(response)

    def fallback_head(self, entry, headers):
        headers = dict(headers)
        headers.setdefault("Range", "bytes=0-0")
        request = urllib.request.Request(entry.get("url"), headers=headers, method="GET")
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            xbmc.log("AIOStreams forwarder HEAD fallback failed: %s" % exc, xbmc.LOGWARNING)
            self.send_error(502, "Upstream playback error")
            return
        with response:
            self.send_response(response.getcode())
            copy_headers(self, response)
            self.end_headers()

    def copy_body(self, response):
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, socket.error):
                break


def run():
    monitor = xbmc.Monitor()
    player = xbmc.Player()
    resume_state = {
        "active_key": "",
        "entry": None,
        "last_save": 0,
        "resume_at": 0,
        "resume_seek_done": False,
        "resume_seek_started": 0,
        "resume_last_seek": 0,
        "resume_seek_attempts": 0,
        "last_position": 0,
    }
    port = setting_int("forwarder_port", 45987)
    try:
        server = ForwarderServer(("127.0.0.1", port), ForwarderHandler)
    except OSError as exc:
        xbmc.log("AIOStreams forwarder could not bind 127.0.0.1:%s: %s" % (port, exc), xbmc.LOGERROR)
        while not monitor.waitForAbort(10):
            pass
        return

    thread = threading.Thread(target=server.serve_forever, name="AIOStreamsForwarder")
    thread.daemon = True
    thread.start()
    xbmc.log("AIOStreams forwarder listening on 127.0.0.1:%s" % port, xbmc.LOGINFO)

    try:
        while not monitor.abortRequested():
            record_resume(player, resume_state)
            if monitor.waitForAbort(0.25):
                break
        record_resume(player, resume_state)
        xbmc.sleep(250)
        record_resume(player, resume_state)
        xbmc.sleep(250)
        record_resume(player, resume_state)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


if __name__ == "__main__":
    run()
