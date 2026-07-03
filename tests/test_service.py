import service


class FakeResponse:
    def __init__(self, headers):
        self._headers = headers

    @property
    def headers(self):
        return self

    def get(self, key):
        return self._headers.get(key)


def test_should_keep_resume():
    assert not service.should_keep_resume(30, 3600)
    assert service.should_keep_resume(120, 3600)
    assert not service.should_keep_resume(3400, 3600)  # >= 90% watched
    assert service.should_keep_resume(120, 0)  # unknown duration


def test_is_completed_resume():
    assert service.is_completed_resume(3400, 3600)
    assert not service.is_completed_resume(120, 3600)
    assert not service.is_completed_resume(120, 0)


def test_range_start():
    assert service.range_start("bytes=100-") == 100
    assert service.range_start("bytes=0-499") == 0
    assert service.range_start("") is None
    assert service.range_start(None) is None


def test_response_body_bounds_content_range():
    response = FakeResponse({"Content-Range": "bytes 100-199/500"})
    assert service.response_body_bounds(response, {}) == (100, 199)


def test_response_body_bounds_content_length():
    response = FakeResponse({"Content-Length": "500"})
    assert service.response_body_bounds(response, {"Range": "bytes=100-"}) == (100, 599)


def test_response_body_bounds_unknown():
    response = FakeResponse({})
    assert service.response_body_bounds(response, {}) == (0, None)


def test_token_from_path():
    assert service.token_from_path("/play/abc123/file.mkv") == "abc123"
    assert service.token_from_path("/play/abc%2F123/file.mkv") == "abc/123"
    assert service.token_from_path("/other/abc") == ""
    assert service.token_from_path("/") == ""


def test_resume_key_is_stable():
    key_one = service.resume_key("http://u", "tt1", "stream")
    key_two = service.resume_key("http://u", "tt1", "stream")
    assert key_one == key_two
    assert key_one != service.resume_key("http://u", "tt2", "stream")


def test_entry_from_context_defaults():
    entry = service.entry_from_context({
        "url": "http://u",
        "resume": "61",
        "duration": "3600",
        "video_id": "tt1:1:2",
        "show_id": "tt1",
        "show_imdb": "tt1",
        "season": "1",
        "episode": "2",
    })
    assert entry["position"] == 61
    assert entry["duration"] == 3600
    assert entry["title"] == "Untitled"
    assert entry["key"] == service.resume_key("http://u", "", "")
    assert entry["video_id"] == "tt1:1:2"
    assert entry["show_id"] == "tt1"
    assert entry["show_imdb"] == "tt1"
    assert entry["season"] == "1"
    assert entry["episode"] == "2"


def test_sanitize_playback_headers_matches_addon():
    headers = {"Range": "bytes=0-", "If-Range": "x", "Referer": "http://r"}
    assert service.sanitize_playback_headers(headers) == {"Referer": "http://r"}
