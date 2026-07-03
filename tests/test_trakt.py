from resources.lib import trakt


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
