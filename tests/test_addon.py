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
