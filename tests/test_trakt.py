from resources.lib import trakt


def test_trakt_enabled_can_come_from_credentials_file(monkeypatch):
    monkeypatch.setattr(trakt, "setting", lambda key: "")
    monkeypatch.setattr(trakt, "load_credentials", lambda: {
        "trakt_enabled": True,
        "trakt_client_id": "client",
        "trakt_client_secret": "secret",
    })

    assert trakt.configured()
    assert trakt.enabled()


def test_trakt_scrobble_can_be_disabled_from_credentials_file(monkeypatch):
    monkeypatch.setattr(trakt, "setting", lambda key: "true" if key == "trakt_enabled" else "")
    monkeypatch.setattr(trakt, "load_credentials", lambda: {
        "trakt_scrobble": False,
        "trakt_client_id": "client",
        "trakt_client_secret": "secret",
    })

    assert trakt.enabled()
    assert not trakt.scrobble_enabled()


def test_movie_scrobble_payload_from_imdb_id():
    payload = trakt.scrobble_payload({
        "item_type": "movie",
        "item_id": "tt0111161",
    }, 1800, 7200)

    assert payload == {
        "movie": {"ids": {"imdb": "tt0111161"}},
        "progress": 25.0,
    }


def test_episode_scrobble_payload_from_stremio_id():
    payload = trakt.scrobble_payload({
        "item_type": "series",
        "item_id": "tt0944947:1:2",
        "video_id": "tt0944947:1:2",
    }, 900, 3600)

    assert payload == {
        "episode": {"season": 1, "number": 2},
        "show": {"ids": {"imdb": "tt0944947"}},
        "progress": 25.0,
    }


def test_episode_scrobble_payload_prefers_explicit_show_fields():
    payload = trakt.scrobble_payload({
        "item_type": "series",
        "item_id": "tt1480055",
        "show_imdb": "tt0944947",
        "season": "1",
        "episode": "2",
    }, 1800, 3600)

    assert payload == {
        "episode": {"season": 1, "number": 2, "ids": {"imdb": "tt1480055"}},
        "show": {"ids": {"imdb": "tt0944947"}},
        "progress": 50.0,
    }


def test_scrobble_payload_requires_identifiable_media():
    assert trakt.scrobble_payload({"item_type": "movie", "item_id": "kitsu:1"}, 10, 100) == {}
    assert trakt.scrobble_payload({"item_type": "series", "item_id": "tt0944947"}, 10, 100) == {}


def test_sync_paths_include_extended_info():
    calls = []

    def fake_api_request(path):
        calls.append(path)
        return []

    original = trakt.api_request
    trakt.api_request = fake_api_request
    try:
        assert trakt.playback("movies") == []
        assert trakt.watchlist("shows") == []
        assert trakt.history("episodes") == []
        assert trakt.watched("movies") == []
        assert trakt.show_progress("tt7654321") == []
    finally:
        trakt.api_request = original

    assert calls == [
        "/sync/playback/movies?extended=full&limit=50",
        "/sync/watchlist/shows/rank/asc?extended=full&limit=50",
        "/sync/history/episodes?extended=full&limit=50",
        "/sync/watched/movies?extended=full",
        "/shows/tt7654321/progress/watched?hidden=false&specials=false&count_specials=false&extended=full",
    ]


def test_remove_playback_uses_delete(monkeypatch):
    calls = []

    def fake_api_request(path, method="GET", body=None, oauth=True):
        calls.append((path, method, body, oauth))
        return {}

    monkeypatch.setattr(trakt, "api_request", fake_api_request)

    assert trakt.remove_playback(123)
    assert calls == [("/sync/playback/123", "DELETE", None, True)]
