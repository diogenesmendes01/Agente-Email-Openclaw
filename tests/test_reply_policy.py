"""Tests for orchestrator.utils.reply_policy."""
import pytest


class TestIsNoReplySender:
    @pytest.mark.parametrize("addr,expected", [
        ("noreply@github.com", True),
        ("no-reply@stripe.com", True),
        ("no_reply@example.com", True),
        ("donotreply@bank.com", True),
        ("do-not-reply@aws.com", True),
        ("mailer-daemon@gmail.com", True),
        ("postmaster@example.com", True),
        ("notifications@github.com", True),
        ("notification@linkedin.com", True),
        ("alerts@grafana.com", True),
        ("alert@pagerduty.com", True),
        ("news@medium.com", True),
        ("newsletter@substack.com", True),
        ("bounce@mailchimp.com", True),
        ("automated@bot.com", True),
        ("system@app.com", True),
        ("Joe Doe <noreply@x.com>", True),
        ("NOREPLY@upper.com", True),
        ("john@gmail.com", False),
        ("contact@company.com", False),
        ("support@stripe.com", False),
        ("ana.silva@empresa.com.br", False),
        ("", False),
        (None, False),
    ])
    def test_detection(self, addr, expected):
        from orchestrator.utils.reply_policy import is_no_reply_sender
        assert is_no_reply_sender(addr) is expected


class TestIsNonReplyableCategory:
    @pytest.mark.parametrize("cat,expected", [
        ("newsletter", True),
        ("promocao", True),
        ("notificacao_automatica", True),
        ("transacional", True),
        ("NEWSLETTER", True),
        ("cliente", False),
        ("financeiro", False),
        ("trabalho", False),
        ("", False),
        (None, False),
    ])
    def test_category(self, cat, expected):
        from orchestrator.utils.reply_policy import is_non_replyable_category
        assert is_non_replyable_category(cat) is expected
