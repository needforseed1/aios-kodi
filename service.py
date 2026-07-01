import json
import http.client
import os
import re
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
RESUME_SAVE_INTERVAL = 30
CONTEXT_CHECK_INTERVAL = 2
FORWARDER_CHUNK_SIZE = 256 * 1024
FORWARDER_READ_RETRIES = 3
CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.I)
RANGE_START_RE = re.compile(r"bytes=(\d+)-", re.I)


class ForwarderServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


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
        atomic_json_dump(path, {"entries": entries}, indent=2, sort_keys=True)
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
    if is_completed_resume(position, duration):
        return False
    return True


def is_completed_resume(position, duration):
    return bool(duration and duration > 0 and position >= duration * 0.9)


def load_current_playback():
    data = load_json(CURRENT_PLAYBACK_FILE, {})
    return data if isinstance(data, dict) else {}


def clear_current_playback():
    path = profile_path(CURRENT_PLAYBACK_FILE)
    try:
        atomic_json_dump(path, {})
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
        "duration": safe_int(context.get("duration")),
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


def range_start(value):
    match = RANGE_START_RE.match(value or "")
    return safe_int(match.group(1)) if match else None


def response_body_bounds(response, request_headers):
    content_range = response.headers.get("Content-Range") or ""
    match = CONTENT_RANGE_RE.match(content_range)
    if match:
        return safe_int(match.group(1)), safe_int(match.group(2))

    start = range_start(request_headers.get("Range")) or 0
    content_length = safe_int(response.headers.get("Content-Length"))
    if content_length > 0:
        return start, start + content_length - 1
    return start, None


def retryable_read_error(exc):
    return isinstance(exc, (
        http.client.HTTPException,
        OSError,
        RuntimeError,
        socket.timeout,
        TimeoutError,
        urllib.error.URLError,
    ))


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
        # Reading the context file 4x/second wears flash for nothing; poll
        # fast only until an entry is picked up, then every couple seconds
        # to catch playlist advances.
        now = time.time()
        if state.get("entry") is None or now - float(state.get("last_context_check") or 0) >= CONTEXT_CHECK_INTERVAL:
            state["last_context_check"] = now
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
        entry["duration"] = duration or safe_int(entry.get("duration"))
        resume_duration = safe_int(entry.get("duration"))
        now = time.time()
        last_position = safe_int(state.get("last_position"))
        position_jump = last_position and abs(position - last_position) >= 30
        state["last_position"] = position
        # In-memory entry tracks every tick; the on-disk rewrite is throttled
        # to spare flash. Seeks flush immediately, and stop/exit finalizes
        # below, so at most RESUME_SAVE_INTERVAL of linear playback is lost
        # on a hard crash.
        if position_jump or now - float(state.get("last_save") or 0) >= RESUME_SAVE_INTERVAL:
            if should_keep_resume(position, resume_duration):
                upsert_resume_entry(entry)
                xbmc.log("AIOStreams resume saved at %ss/%ss" % (position, resume_duration), xbmc.LOGDEBUG)
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
        elif is_completed_resume(position, duration):
            remove_resume_entry(active_key)
            xbmc.log("AIOStreams resume cleared after completion at %ss/%ss" % (position, duration), xbmc.LOGINFO)
        else:
            xbmc.log("AIOStreams resume kept after early stop at %ss/%ss" % (position, duration), xbmc.LOGINFO)
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
                self.copy_body(entry, headers, response)

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

    def copy_body(self, entry, headers, response):
        offset, expected_end = response_body_bounds(response, headers)
        retries = 0
        current_response = response
        close_current = False
        try:
            while True:
                read_error = None
                chunk = b""
                try:
                    chunk = current_response.read(FORWARDER_CHUNK_SIZE)
                except http.client.IncompleteRead as exc:
                    chunk = exc.partial or b""
                    read_error = exc
                except (http.client.HTTPException, OSError, socket.timeout, TimeoutError, urllib.error.URLError) as exc:
                    read_error = exc

                if chunk:
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, socket.error):
                        return
                    offset += len(chunk)

                if read_error:
                    xbmc.log("AIOStreams forwarder read interrupted at byte %s: %s" % (offset, read_error), xbmc.LOGWARNING)
                elif not chunk:
                    if expected_end is not None and offset <= expected_end:
                        read_error = RuntimeError("upstream ended early at byte %s of %s" % (offset, expected_end + 1))
                        xbmc.log("AIOStreams forwarder upstream ended early at byte %s of %s" % (offset, expected_end + 1), xbmc.LOGWARNING)
                    else:
                        return

                if not read_error:
                    continue
                if not retryable_read_error(read_error) or retries >= FORWARDER_READ_RETRIES:
                    xbmc.log("AIOStreams forwarder giving up at byte %s after %s retries" % (offset, retries), xbmc.LOGWARNING)
                    return

                retries += 1
                retry_headers = dict(headers)
                retry_headers["Range"] = "bytes=%d-" % offset
                try:
                    retry_request = urllib.request.Request(entry.get("url"), headers=retry_headers, method="GET")
                    retry_response = urllib.request.urlopen(retry_request, timeout=30)
                except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                    xbmc.log("AIOStreams forwarder retry %s failed at byte %s: %s" % (retries, offset, exc), xbmc.LOGWARNING)
                    continue

                if offset > 0 and retry_response.getcode() != 206:
                    xbmc.log("AIOStreams forwarder retry %s ignored Range at byte %s with HTTP %s" % (
                        retries,
                        offset,
                        retry_response.getcode(),
                    ), xbmc.LOGWARNING)
                    retry_response.close()
                    return

                if close_current:
                    current_response.close()
                current_response = retry_response
                close_current = True
                _, retry_expected_end = response_body_bounds(current_response, retry_headers)
                if retry_expected_end is not None:
                    expected_end = retry_expected_end
                xbmc.log("AIOStreams forwarder resumed upstream at byte %s retry=%s" % (offset, retries), xbmc.LOGINFO)
        finally:
            if close_current:
                current_response.close()


def run():
    monitor = xbmc.Monitor()
    player = xbmc.Player()
    resume_state = {
        "active_key": "",
        "entry": None,
        "last_save": 0,
        "last_context_check": 0,
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
