from parad.connection import generate_url, parse_url, redact_url


def test_canonical_token_url_round_trips():
    url = generate_url(
        "cnine",
        passphrase="secret phrase",
        gateway_url="https://paradoxdb.onrender.com/v1",
        project="workspace",
        token="pk_live_test",
    )
    parsed = parse_url(url)
    assert parsed["name"] == "cnine"
    assert parsed["project"] == "workspace"
    assert parsed["gateway_url"] == "https://paradoxdb.onrender.com/v1"
    assert parsed["token"] == "pk_live_test"
    assert parsed["passphrase"] == "secret phrase"


def test_redact_url_removes_secret_material():
    url = "parad://pk_live_test@local/workspace/cnine?gateway=https://g/v1&passphrase=secret"
    redacted = redact_url(url)
    assert "pk_live_test" not in redacted
    assert "secret" not in redacted
    assert "workspace/cnine" in redacted
    assert "gateway=https://g/v1" in redacted
