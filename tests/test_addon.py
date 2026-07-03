import addon


def test_endpoint_base_value_strips_manifest():
    assert addon.endpoint_base_value("https://x.example/stremio/abc/manifest.json") == "https://x.example/stremio/abc"
    assert addon.endpoint_base_value("https://x.example/stremio/abc/") == "https://x.example/stremio/abc"
    assert addon.endpoint_base_value("") == ""
    assert addon.endpoint_base_value(None) == ""


def test_redact_url():
    assert addon.redact_url("https://host.example/secret/uuid?token=1") == "https://host.example/..."
    assert addon.redact_url("not a url") == "not a url"
    assert addon.redact_url("") == ""


def test_runtime_seconds():
    assert addon.runtime_seconds(90) == 5400
    assert addon.runtime_seconds("1h 30m") == 5400
    assert addon.runtime_seconds("45 min") == 2700
    assert addon.runtime_seconds("2h") == 7200
    assert addon.runtime_seconds("") == 0
    assert addon.runtime_seconds(None) == 0


def test_parse_stremio_episode_id():
    assert addon.parse_stremio_episode_id("tt123:2:5") == ("2", "5")
    assert addon.parse_stremio_episode_id("kitsu:456:1:12") == ("1", "12")
    assert addon.parse_stremio_episode_id("tt123") is None
    assert addon.parse_stremio_episode_id("") is None


def test_video_season_and_episode():
    assert addon.video_season({"season": 3}) == "3"
    assert addon.video_season({"id": "tt1:4:2"}) == "4"
    assert addon.video_season({"id": "tt1"}) is None
    assert addon.video_episode({"episode": 7}) == "7"
    assert addon.video_episode({"id": "tt1:4:2"}) == "2"


def test_format_size():
    assert addon.format_size(None) == ""
    assert addon.format_size(0) == ""
    assert addon.format_size(2048) == "2 KB"
    assert addon.format_size(5.5 * 1024 ** 3) == "5.5 GB"


def test_sanitize_playback_headers():
    headers = {"Range": "bytes=0-", "content-length": "5", "Authorization": "Basic x", "empty": None}
    assert addon.sanitize_playback_headers(headers) == {"Authorization": "Basic x"}
    assert addon.sanitize_playback_headers("nope") == {}


def test_with_query_param():
    assert addon.with_query_param("http://a/b", "k", "v") == "http://a/b?k=v"
    assert addon.with_query_param("http://a/b?x=1", "k", "v") == "http://a/b?x=1&k=v"


def test_safe_filename():
    assert addon.safe_filename("a/b:c*d?e.mkv") == "a.b.c.d.e.mkv"
    assert addon.safe_filename("") == "stream.mkv"
    assert addon.safe_filename("noext") == "noext.mkv"


def test_resume_episode_title():
    assert addon.resume_episode_title("Show", "1", "2", "Pilot") == "Show - S01E02 - Pilot"
    assert addon.resume_episode_title("", "1", "2", "Pilot") == "S01E02 - Pilot"
    assert addon.resume_episode_title("Show", "sp", "", "Pilot") == "Show - Pilot"


def test_best_stream_id():
    assert addon.best_stream_id({"imdb_id": "tt42"}, "fallback") == "tt42"
    assert addon.best_stream_id({"imdb_id": "kitsu:9"}, "fallback") == "fallback"
    assert addon.best_stream_id({}, "fallback") == "fallback"


def test_season_sort_key_orders_numeric_before_text():
    values = ["2", "special", "10", "1"]
    assert sorted(values, key=addon.season_sort_key) == ["1", "2", "10", "special"]


def test_kodi_builtin_headers_do_not_mutate_input():
    base = {"Authorization": "Basic x"}
    merged = addon.kodi_builtin_headers(base)
    assert merged["Connection"] == "close"
    assert "Connection" not in base


def test_playback_url_and_headers_extracts_basic_auth():
    url, headers = addon.playback_url_and_headers(
        "http://original/", "http://user:pass@host:8080/file.mkv", {})
    assert url == "http://host:8080/file.mkv"
    assert headers["Authorization"].startswith("Basic ")


def test_playback_mime():
    assert addon.playback_mime("http://h/file.mkv") == "video/x-matroska"
    assert addon.playback_mime("http://h/file.mp4") == "video/mp4"
    assert addon.playback_mime("http://h/stream", "Movie.2020.REMUX") == "video/x-matroska"
    assert addon.playback_mime("http://h/stream") == ""


def test_play_uses_kodi_direct_by_default(monkeypatch):
    resolved = {}

    monkeypatch.setattr(addon, "setting", lambda key: "")
    monkeypatch.setattr(addon, "resolve_playback_redirect", lambda url, headers: url)
    monkeypatch.setattr(addon, "register_forwarder_url", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("forwarder used")))
    monkeypatch.setattr(addon.xbmcplugin, "setResolvedUrl", lambda handle, succeeded, item: resolved.update({
        "succeeded": succeeded,
        "path": item.path,
    }))

    addon.play("http://stream.example/file.mkv", "{}", 0, {})

    assert resolved["succeeded"] is True
    assert resolved["path"].startswith("http://stream.example/file.mkv|")
    assert "127.0.0.1" not in resolved["path"]


def test_play_uses_kodi_direct_when_forwarder_disabled(monkeypatch):
    resolved = {}

    monkeypatch.setattr(addon, "setting", lambda key: "false" if key == "use_local_forwarder" else "")
    monkeypatch.setattr(addon, "resolve_playback_redirect", lambda url, headers: url)
    monkeypatch.setattr(addon, "register_forwarder_url", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("forwarder used")))
    monkeypatch.setattr(addon.xbmcplugin, "setResolvedUrl", lambda handle, succeeded, item: resolved.update({
        "succeeded": succeeded,
        "path": item.path,
    }))

    addon.play("http://stream.example/file.mkv", "{}", 0, {})

    assert resolved["succeeded"] is True
    assert resolved["path"].startswith("http://stream.example/file.mkv|")
    assert "127.0.0.1" not in resolved["path"]


def test_play_kodi_direct_preserves_basic_auth_url(monkeypatch):
    resolved = {}

    monkeypatch.setattr(addon, "setting", lambda key: "")
    monkeypatch.setattr(addon, "resolve_playback_redirect", lambda url, headers: "http://user:pass@stream.example/file.mkv")
    monkeypatch.setattr(addon, "register_forwarder_url", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("forwarder used")))
    monkeypatch.setattr(addon.xbmcplugin, "setResolvedUrl", lambda handle, succeeded, item: resolved.update({
        "succeeded": succeeded,
        "path": item.path,
    }))

    addon.play("http://user:pass@stream.example/file.mkv", "{}", 0, {})

    assert resolved["succeeded"] is True
    assert resolved["path"].startswith("http://user:pass@stream.example/file.mkv|")
    assert "Authorization=" not in resolved["path"]


def test_play_uses_forwarder_when_enabled(monkeypatch):
    resolved = {}
    calls = []

    def fake_register_forwarder_url(playback_url, playback_headers, context):
        calls.append((playback_url, playback_headers, context))
        return "http://127.0.0.1:45987/play/token/file.mkv"

    monkeypatch.setattr(addon, "setting", lambda key: "true" if key == "use_local_forwarder" else "")
    monkeypatch.setattr(addon, "resolve_playback_redirect", lambda url, headers: url)
    monkeypatch.setattr(addon, "register_forwarder_url", fake_register_forwarder_url)
    monkeypatch.setattr(addon.xbmcplugin, "setResolvedUrl", lambda handle, succeeded, item: resolved.update({
        "succeeded": succeeded,
        "path": item.path,
    }))

    addon.play("http://stream.example/file.mkv", "{}", 0, {})

    assert calls
    assert resolved == {
        "succeeded": True,
        "path": "http://127.0.0.1:45987/play/token/file.mkv",
    }


def test_play_forwarder_converts_basic_auth_to_header(monkeypatch):
    calls = []

    def fake_register_forwarder_url(playback_url, playback_headers, context):
        calls.append((playback_url, playback_headers))
        return "http://127.0.0.1:45987/play/token/file.mkv"

    monkeypatch.setattr(addon, "setting", lambda key: "true" if key == "use_local_forwarder" else "")
    monkeypatch.setattr(addon, "resolve_playback_redirect", lambda url, headers: "http://user:pass@stream.example/file.mkv")
    monkeypatch.setattr(addon, "register_forwarder_url", fake_register_forwarder_url)
    monkeypatch.setattr(addon.xbmcplugin, "setResolvedUrl", lambda *args, **kwargs: None)

    addon.play("http://user:pass@stream.example/file.mkv", "{}", 0, {})

    assert calls
    assert calls[0][0] == "http://stream.example/file.mkv"
    assert calls[0][1]["Authorization"].startswith("Basic ")


def test_clean_html():
    assert addon.clean_html("<p>Hello&amp;   world</p>") == "Hello& world"
    assert addon.clean_html(None) == ""


def test_parallel_map_preserves_order():
    assert addon.parallel_map(lambda value: value * 2, [3, 1, 2]) == [6, 2, 4]
    assert addon.parallel_map(lambda value: value, []) == []


def test_display_rating():
    assert addon.display_rating("7.456") == "7.5"
    assert addon.display_rating(0) == ""
    assert addon.display_rating(None) == ""


def test_trakt_stream_target_movie():
    target = addon.trakt_stream_target({
        "type": "movie",
        "plays": 2,
        "movie": {
            "title": "Movie",
            "year": 2020,
            "runtime": 100,
            "ids": {"imdb": "tt1234567"},
        },
    })

    assert target["type"] == "movie"
    assert target["id"] == "tt1234567"
    assert target["title"] == "Movie"
    assert target["duration"] == 6000
    assert target["info"]["playcount"] == 2


def test_trakt_stream_target_episode():
    target = addon.trakt_stream_target({
        "episode": {
            "title": "Pilot",
            "season": 1,
            "number": 2,
            "runtime": 45,
        },
        "show": {
            "title": "Show",
            "ids": {"imdb": "tt7654321"},
        },
    })

    assert target["type"] == "series"
    assert target["id"] == "tt7654321:1:2"
    assert target["show_imdb"] == "tt7654321"
    assert target["title"] == "Show - S01E02 - Pilot"
    assert target["duration"] == 2700


def test_trakt_progress_position():
    target = {"duration": 3600}
    assert addon.trakt_progress_position({"progress": 25}, target) == 900
    assert addon.trakt_progress_position({"progress": 125}, target) == 3600


def test_trakt_next_entries_uses_next_episode(monkeypatch):
    monkeypatch.setattr(addon.trakt, "show_progress", lambda show_id: {
        "next_episode": {"title": "Next", "season": 1, "number": 3, "runtime": 45},
    })

    targets = addon.trakt_next_entries([{
        "watched_at": "2026-07-01T00:00:00.000Z",
        "show": {"title": "Show", "ids": {"imdb": "tt7654321"}},
    }], [])

    assert len(targets) == 1
    assert targets[0]["id"] == "tt7654321:1:3"
    assert targets[0]["title"] == "Show - S01E03 - Next"


def test_trakt_next_entries_limits_recent_shows(monkeypatch):
    calls = []

    def fake_show_progress(show_id):
        calls.append(show_id)
        return {"next_episode": {"title": "Next", "season": 1, "number": 2}}

    monkeypatch.setattr(addon.trakt, "show_progress", fake_show_progress)
    entries = [
        {"watched_at": "2026-07-%02dT00:00:00.000Z" % day, "show": {"title": "Show %s" % day, "ids": {"imdb": "tt%07d" % day}}}
        for day in range(1, 20)
    ]

    addon.trakt_next_entries(entries, [], limit=5)

    assert len(calls) == 5


def test_trakt_next_entries_skips_paused_resume(monkeypatch):
    monkeypatch.setattr(addon.trakt, "show_progress", lambda show_id: {
        "next_episode": {"title": "Next", "season": 1, "number": 3},
    })

    targets = addon.trakt_next_entries([{
        "watched_at": "2026-07-01T00:00:00.000Z",
        "show": {"title": "Show", "ids": {"imdb": "tt7654321"}},
    }], [{
        "type": "episode",
        "show": {"ids": {"imdb": "tt7654321"}},
        "episode": {"season": 1, "number": 2},
    }])

    assert targets == []


def test_enrich_trakt_target_movie_uses_metadata(monkeypatch):
    monkeypatch.setattr(addon, "cached_service_json", lambda url, service, ttl: {
        "meta": {
            "id": "tt1234567",
            "name": "Metadata Movie",
            "description": "Metadata plot",
            "poster": "http://image/poster.jpg",
            "runtime": 90,
        },
    })

    target = addon.enrich_trakt_target({
        "type": "movie",
        "id": "tt1234567",
        "title": "Trakt Movie",
        "info": {"playcount": 1},
    })

    assert target["title"] == "Metadata Movie"
    assert target["art"]["poster"] == "http://image/poster.jpg"
    assert target["info"]["plot"] == "Metadata plot"
    assert target["info"]["playcount"] == 1
    assert target["duration"] == 5400


def test_enrich_trakt_target_episode_uses_series_metadata(monkeypatch):
    monkeypatch.setattr(addon, "cached_service_json", lambda url, service, ttl: {
        "meta": {
            "id": "tt7654321",
            "name": "Metadata Show",
            "poster": "http://image/show.jpg",
            "videos": [{
                "id": "tt7654321:1:3",
                "season": 1,
                "episode": 3,
                "title": "Metadata Next",
                "overview": "Episode plot",
                "thumbnail": "http://image/episode.jpg",
                "runtime": 45,
            }],
        },
    })

    target = addon.enrich_trakt_target({
        "type": "series",
        "id": "tt7654321:1:3",
        "show_imdb": "tt7654321",
        "season": "1",
        "episode": "3",
        "title": "Show - S01E03 - Next",
        "info": {},
    })

    assert target["title"] == "Metadata Show - S01E03 - Metadata Next"
    assert target["art"]["poster"] == "http://image/episode.jpg"
    assert target["info"]["plot"] == "Episode plot"
    assert target["duration"] == 2700


def test_resume_sources_params_uses_saved_movie_id():
    params = addon.resume_sources_params({
        "key": "old-resume-key",
        "item_type": "movie",
        "item_id": "tt1234567",
        "title": "Movie",
        "position": 120,
        "duration": 3600,
    })

    assert params["action"] == "streams"
    assert params["type"] == "movie"
    assert params["id"] == "tt1234567"
    assert params["resume"] == "120"
    assert params["duration"] == "3600"
    assert params["resume_key"] == "old-resume-key"


def test_resume_sources_params_synthesizes_episode_id():
    params = addon.resume_sources_params({
        "item_type": "series",
        "show_imdb": "tt7654321",
        "season": "1",
        "episode": "2",
        "title": "Show - S01E02 - Pilot",
        "position": 120,
    })

    assert params["action"] == "streams"
    assert params["type"] == "series"
    assert params["id"] == "tt7654321:1:2"
    assert params["season"] == "1"
    assert params["episode"] == "2"
    assert params["resume"] == "120"


def test_resume_sources_params_falls_back_to_search():
    params = addon.resume_sources_params({
        "key": "old-resume-key",
        "item_type": "series",
        "title": "Show - S01E02 - Pilot",
        "season": "1",
        "episode": "2",
        "position": 120,
    })

    assert params["action"] == "search"
    assert params["type"] == "series"
    assert params["query"] == "Show"
    assert params["resume"] == "120"
    assert params["season"] == "1"
    assert params["episode"] == "2"
    assert params["resume_key"] == "old-resume-key"


def test_remove_resume_entry_saves_without_key(monkeypatch):
    saved = {}
    monkeypatch.setattr(addon, "load_resume_entries", lambda: [
        {"key": "keep", "title": "Keep"},
        {"key": "remove", "title": "Remove"},
    ])
    monkeypatch.setattr(addon, "save_resume_entries", lambda entries: saved.setdefault("entries", entries))

    assert addon.remove_resume_entry("remove")
    assert saved["entries"] == [{"key": "keep", "title": "Keep"}]


def test_resume_context_menu_marks_watched():
    menu = addon.resume_context_menu({"key": "resume-key"})

    assert len(menu) == 1
    assert menu[0][0] == "Remove from Resume"
    assert "action=resume_mark_watched" in menu[0][1]
    assert "key=resume-key" in menu[0][1]


def test_trakt_resume_context_menu_removes_playback():
    menu = addon.trakt_resume_context_menu({"id": 123})

    assert len(menu) == 1
    assert menu[0][0] == "Remove from Resume"
    assert "action=trakt_resume_remove" in menu[0][1]
    assert "id=123" in menu[0][1]


def test_resume_art_prefers_landscape_for_movies():
    art = addon.resume_art({"item_type": "movie"}, {
        "poster": "http://image/poster.jpg",
        "fanart": "http://image/fanart.jpg",
    })

    assert art["thumb"] == "http://image/fanart.jpg"
    assert art["thumbnail"] == "http://image/fanart.jpg"
    assert art["icon"] == "http://image/fanart.jpg"
    assert art["landscape"] == "http://image/fanart.jpg"
    assert art["poster"] == "http://image/poster.jpg"


def test_resume_art_keeps_episode_art():
    art = addon.resume_art({"item_type": "series"}, {"thumb": "http://image/episode.jpg"})

    assert art == {"thumb": "http://image/episode.jpg"}


def test_add_playable_can_replace_context_menu(monkeypatch):
    captured = {}

    def fake_add_directory_item(handle, url, item, is_folder=False):
        captured["item"] = item
        return True

    monkeypatch.setattr(addon.xbmcplugin, "addDirectoryItem", fake_add_directory_item)

    addon.add_playable("Resume", {"action": "noop"}, context_menu=[("Mark as watched", "RunPlugin(x)")], replace_context=True)

    assert captured["item"].context_menu == [("Mark as watched", "RunPlugin(x)")]
    assert captured["item"].context_menu_replace is True


def test_list_resume_appends_context_menu(monkeypatch):
    captured = {}

    def fake_add_directory_item(handle, url, item, is_folder=False):
        captured["item"] = item
        captured["url"] = url
        return True

    monkeypatch.setattr(addon, "load_resume_entries", lambda: [{
        "key": "resume-key",
        "url": "http://stream/file.mkv",
        "title": "Resume Movie",
        "item_type": "movie",
        "item_id": "tt1234567",
        "position": 120,
        "duration": 3600,
    }])
    monkeypatch.setattr(addon, "load_stream_contexts", lambda: {"contexts": {}})
    monkeypatch.setattr(addon, "save_stream_contexts", lambda data: None)
    monkeypatch.setattr(addon, "trakt_enabled_for_lists", lambda: False)
    monkeypatch.setattr(addon, "set_view", lambda *args, **kwargs: None)
    monkeypatch.setattr(addon.xbmcplugin, "addDirectoryItem", fake_add_directory_item)

    addon.list_resume()

    assert captured["item"].context_menu[0][0] == "Remove from Resume"
    assert captured["item"].context_menu_replace is False


def test_add_trakt_progress_entry_has_remove_context_menu(monkeypatch):
    captured = {}

    def fake_add_directory_item(handle, url, item, is_folder=False):
        captured["item"] = item
        return True

    monkeypatch.setattr(addon, "enrich_trakt_target", lambda target: target)
    monkeypatch.setattr(addon.xbmcplugin, "addDirectoryItem", fake_add_directory_item)

    added = addon.add_trakt_progress_entry({
        "id": 456,
        "type": "movie",
        "progress": 25,
        "movie": {
            "title": "Trakt Movie",
            "runtime": 120,
            "ids": {"imdb": "tt1234567"},
        },
    })

    assert added
    assert captured["item"].context_menu[0][0] == "Remove from Resume"
    assert "action=trakt_resume_remove" in captured["item"].context_menu[0][1]
    assert "id=456" in captured["item"].context_menu[0][1]
    assert captured["item"].context_menu_replace is False


def test_progress_create_falls_back_to_single_message():
    calls = []

    class LegacyProgress:
        def create(self, *args):
            if len(args) > 2:
                raise TypeError("function takes at most 2 arguments")
            calls.append(args)

    addon.progress_create(LegacyProgress(), "Heading", "Line one\nLine two")

    assert calls == [("Heading", "Line one\nLine two")]


def test_list_streams_preserves_resume_key(monkeypatch):
    captured = {}

    def fake_register_stream_context(context, data=None, save=True):
        captured.update(context)
        return "token"

    monkeypatch.setattr(addon, "aiostreams_base", lambda: "http://aiostreams")
    monkeypatch.setattr(addon, "get_json", lambda url: {
        "streams": [{"url": "http://stream/file.mkv", "title": "Source"}],
    })
    monkeypatch.setattr(addon, "load_stream_contexts", lambda: {"contexts": {}})
    monkeypatch.setattr(addon, "save_stream_contexts", lambda data: None)
    monkeypatch.setattr(addon, "register_stream_context", fake_register_stream_context)
    monkeypatch.setattr(addon, "add_playable", lambda *args, **kwargs: None)
    monkeypatch.setattr(addon, "set_view", lambda *args, **kwargs: None)

    addon.list_streams("movie", "tt1234567", resume=120, duration=3600, resume_key="old-resume-key")

    assert captured["key"] == "old-resume-key"
    assert captured["resume"] == 120
    assert captured["duration"] == 3600


def test_list_streams_omits_empty_resume_key(monkeypatch):
    captured = {}

    def fake_register_stream_context(context, data=None, save=True):
        captured.update(context)
        return "token"

    monkeypatch.setattr(addon, "aiostreams_base", lambda: "http://aiostreams")
    monkeypatch.setattr(addon, "get_json", lambda url: {
        "streams": [{"url": "http://stream/file.mkv", "title": "Source"}],
    })
    monkeypatch.setattr(addon, "load_stream_contexts", lambda: {"contexts": {}})
    monkeypatch.setattr(addon, "save_stream_contexts", lambda data: None)
    monkeypatch.setattr(addon, "register_stream_context", fake_register_stream_context)
    monkeypatch.setattr(addon, "add_playable", lambda *args, **kwargs: None)
    monkeypatch.setattr(addon, "set_view", lambda *args, **kwargs: None)

    addon.list_streams("movie", "tt1234567")

    assert "key" not in captured


def test_list_streams_resume_without_playable_streams_opens_search(monkeypatch):
    commands = []

    monkeypatch.setattr(addon, "aiostreams_base", lambda: "http://aiostreams")
    monkeypatch.setattr(addon, "get_json", lambda url: {"streams": [{"title": "Unsupported"}]})
    monkeypatch.setattr(addon.xbmc, "executebuiltin", lambda command, wait=False: commands.append(command))
    monkeypatch.setattr(addon, "notify", lambda message: (_ for _ in ()).throw(AssertionError(message)))
    monkeypatch.setattr(addon, "set_view", lambda *args, **kwargs: None)

    addon.list_streams(
        "series",
        "tt7654321:1:2",
        content_title="Show - S01E02 - Pilot",
        season="1",
        episode="2",
        resume=120,
        duration=3600,
        resume_key="resume-key",
    )

    assert commands
    assert commands[0].startswith("Container.Update(")
    assert "action=search" in commands[0]
    assert "query=Show" in commands[0]
    assert "resume=120" in commands[0]
    assert "resume_key=resume-key" in commands[0]


def test_list_streams_resume_http_error_opens_search(monkeypatch):
    commands = []

    monkeypatch.setattr(addon, "aiostreams_base", lambda: "http://aiostreams")
    monkeypatch.setattr(addon, "get_json", lambda url: (_ for _ in ()).throw(RuntimeError("HTTP 404 from http://aiostreams/...")))
    monkeypatch.setattr(addon.xbmc, "executebuiltin", lambda command, wait=False: commands.append(command))
    monkeypatch.setattr(addon, "error", lambda message: (_ for _ in ()).throw(AssertionError(message)))
    monkeypatch.setattr(addon, "set_view", lambda *args, **kwargs: None)

    addon.list_streams("movie", "tt1234567", content_title="Movie", resume=120)

    assert commands
    assert "action=search" in commands[0]
    assert "query=Movie" in commands[0]
    assert "resume=120" in commands[0]


def test_trakt_watched_episode_ids(monkeypatch):
    monkeypatch.setattr(addon, "cached_trakt_watched", lambda media_type: [{
        "show": {"ids": {"imdb": "tt7654321"}},
        "seasons": [{
            "number": 1,
            "episodes": [{"number": 2}, {"number": 3}],
        }],
    }])

    assert addon.trakt_watched_episode_ids("tt7654321") == {"tt7654321:1:2", "tt7654321:1:3"}
    assert addon.trakt_watched_episode_ids("tt0000000") == set()


def test_trakt_watched_episode_ids_falls_back_to_show_progress(monkeypatch):
    monkeypatch.setattr(addon, "cached_trakt_watched", lambda media_type: [])
    monkeypatch.setattr(addon, "trakt_enabled_for_lists", lambda: True)
    monkeypatch.setattr(addon.trakt, "show_progress", lambda show_id: {
        "seasons": [{
            "number": 1,
            "episodes": [
                {"number": 2, "completed": True},
                {"number": 3, "completed": False},
            ],
        }],
    })

    assert addon.trakt_watched_episode_ids("tt7654321") == {"tt7654321:1:2"}
