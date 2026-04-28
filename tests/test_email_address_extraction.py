"""Tests for robust email address extraction + owner detection in threads."""
import pytest

from orchestrator.utils.email_parser import extract_email_address, emails_match
from orchestrator.services.llm_service import LLMService


class TestExtractEmailAddress:

    def test_extracts_from_angle_brackets(self):
        assert extract_email_address("Diogenes Mendes <me@domain.com>") == "me@domain.com"

    def test_extracts_from_quoted_name(self):
        assert extract_email_address('"José Silva" <jose@example.com>') == "jose@example.com"

    def test_extracts_bare_email(self):
        assert extract_email_address("me@domain.com") == "me@domain.com"

    def test_normalizes_case(self):
        assert extract_email_address("Me@Domain.COM") == "me@domain.com"

    def test_empty_returns_empty(self):
        assert extract_email_address("") == ""
        assert extract_email_address(None) == ""

    def test_no_email_returns_empty(self):
        assert extract_email_address("just a name, no email") == ""

    def test_picks_first_of_multiple(self):
        assert extract_email_address("<a@b.com>, <c@d.com>") == "a@b.com"


class TestEmailsMatch:

    def test_exact_match(self):
        assert emails_match("me@domain.com", "me@domain.com")

    def test_match_ignores_display_name(self):
        assert emails_match("Diogenes <me@domain.com>", "me@domain.com")
        assert emails_match("me@domain.com", '"Name" <me@domain.com>')

    def test_match_ignores_case(self):
        assert emails_match("ME@DOMAIN.COM", "me@domain.com")

    # Regression tests — these used to produce false positives with `in` substring.

    def test_substring_prefix_false_positive(self):
        """admin@x.com should NOT match admin@xavier.com."""
        assert not emails_match("admin@x.com", "admin@xavier.com")

    def test_substring_suffix_false_positive(self):
        """dgs@hotmail.com should NOT match dgs@hotmail.com.br."""
        assert not emails_match("dgs@hotmail.com", "dgs@hotmail.com.br")

    def test_different_user_same_domain(self):
        assert not emails_match("a@domain.com", "b@domain.com")

    def test_empty_never_matches(self):
        assert not emails_match("", "me@domain.com")
        assert not emails_match("me@domain.com", "")
        assert not emails_match("", "")


class TestFormatThreadContext:
    """Test LLMService._format_thread_context tags the owner's messages."""

    def setup_method(self):
        self.svc = LLMService()

    def test_empty_thread_returns_empty(self):
        assert self.svc._format_thread_context([], "me@domain.com") == ""

    def test_tags_owner_messages_with_you_marker(self):
        thread = [
            {"from": "Other <other@x.com>", "from_email": "other@x.com", "date": "D1", "body": "Hi"},
            {"from": "Me <me@domain.com>", "from_email": "me@domain.com", "date": "D2", "body": "Hello back"},
        ]
        result = self.svc._format_thread_context(thread, "me@domain.com")
        # Owner's message tagged
        assert "Msg 2 [VOCE]" in result
        # Non-owner message NOT tagged
        assert "Msg 1 ---" in result
        assert "Msg 1 [VOCE]" not in result

    def test_includes_body_preview_and_date(self):
        thread = [
            {"from": "x@y.com", "from_email": "x@y.com", "date": "2026-01-01", "body": "Message body text"}
        ]
        result = self.svc._format_thread_context(thread, "")
        assert "2026-01-01" in result
        assert "Message body text" in result

    def test_truncates_body_to_500_chars(self):
        # Use 'Z' so header text ("HISTORICO DA THREAD...") doesn't collide.
        long_body = "Z" * 1000
        thread = [{"from": "x@y.com", "from_email": "x@y.com", "body": long_body}]
        result = self.svc._format_thread_context(thread, "")
        # 500 Zs from body preview should be present but not 1000
        assert "Z" * 500 in result
        assert "Z" * 501 not in result

    def test_no_owner_email_never_tags(self):
        thread = [{"from": "me@domain.com", "from_email": "me@domain.com", "body": "x"}]
        result = self.svc._format_thread_context(thread, "")
        assert "[VOCE]" not in result

    def test_uses_extract_for_owner_match_not_substring(self):
        """Owner admin@x.com should NOT match msg from admin@xavier.com."""
        thread = [
            {"from": "Admin <admin@xavier.com>", "from_email": "admin@xavier.com", "body": "x"}
        ]
        result = self.svc._format_thread_context(thread, "admin@x.com")
        assert "[VOCE]" not in result


class TestPromptSectionMarkers:
    """Thread header used by the formatter MUST match what _manage_prompt_size
    looks for when truncating. Catches header drift that broke truncation in
    the past (formatter emitted 'HISTORICO DA THREAD' but truncator searched
    for 'EMAILS ANTERIORES DESTA THREAD')."""

    def setup_method(self):
        self.svc = LLMService()

    def test_format_thread_context_emits_canonical_header(self):
        thread = [{"from": "a@b.com", "from_email": "a@b.com", "body": "x"}]
        result = self.svc._format_thread_context(thread, "")
        assert self.svc.THREAD_SECTION_HEADER in result

    def test_truncation_removes_thread_section_when_prompt_too_large(self):
        # Build a fake oversized prompt that contains both markers.
        padding = "x" * (self.svc.MAX_PROMPT_TOKENS * 4 + 100)  # > limit
        prompt = (
            f"PROMPT HEADER\n\n"
            f"{self.svc.THREAD_SECTION_HEADER}\n"
            f"--- Msg 1 ---\nDe: a@b.com\nTexto: {padding}\n\n"
            f"{self.svc.CURRENT_EMAIL_HEADER}\n"
            f"De: x@y.com\nAssunto: T\nCorpo: short\n"
        )
        result = self.svc._manage_prompt_size(prompt)
        # Thread section must have been stripped
        assert self.svc.THREAD_SECTION_HEADER not in result
        # Current email section must remain
        assert self.svc.CURRENT_EMAIL_HEADER in result

    def test_small_prompt_not_truncated(self):
        """Prompts under the limit pass through unchanged."""
        prompt = f"{self.svc.THREAD_SECTION_HEADER}\nfoo\n{self.svc.CURRENT_EMAIL_HEADER}\nbar"
        assert self.svc._manage_prompt_size(prompt) == prompt
