import headers


def test_summary_shows_identity_values_but_never_authorization():
    s = headers.summarize([(b"Authorization", b"Bearer secret"), (b"x-agent-user-id", b"u" * 36), (b"Host", b"x")])
    assert s["names"] == ["authorization", "host", "x-agent-user-id"]
    assert s["values"]["x-agent-user-id"] == "u" * 36
    assert s["values"]["x-ms-user-identity"] is None
    assert "secret" not in str(s)


def test_summary_accepts_str_pairs():
    assert headers.summarize([("traceparent", "00-abc-def-01")])["values"]["traceparent"] == "00-abc-def-01"
