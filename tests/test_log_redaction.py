import pytest


class TestRedactSensitive:
    def test_redacts_top_level_token(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        out = redact_sensitive({"token": "abc123", "ok": True})
        assert out == {"token": "<REDACTED>", "ok": True}

    def test_redacts_case_insensitive(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        out = redact_sensitive({"Token": "x", "AUTHORIZATION": "y"})
        assert out["Token"] == "<REDACTED>"
        assert out["AUTHORIZATION"] == "<REDACTED>"

    def test_redacts_nested_dict(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        out = redact_sensitive({
            "level1": {"password": "secret", "ok": 1},
        })
        assert out["level1"]["password"] == "<REDACTED>"
        assert out["level1"]["ok"] == 1

    def test_redacts_list_of_dicts(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        out = redact_sensitive({
            "items": [{"api_key": "k1"}, {"api_key": "k2", "name": "x"}]
        })
        assert out["items"][0]["api_key"] == "<REDACTED>"
        assert out["items"][1]["name"] == "x"

    def test_full_key_list(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        keys = ["token", "authorization", "password", "secret",
                "api_key", "access_token", "refresh_token", "cookie"]
        d = {k: "value" for k in keys}
        out = redact_sensitive(d)
        for k in keys:
            assert out[k] == "<REDACTED>"

    def test_does_not_mutate_input(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        original = {"token": "a"}
        redact_sensitive(original)
        assert original == {"token": "a"}

    def test_handles_non_dict_input_gracefully(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        assert redact_sensitive(None) is None
        assert redact_sensitive("string") == "string"
        assert redact_sensitive([1, 2]) == [1, 2]
