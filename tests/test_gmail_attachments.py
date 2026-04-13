import pytest
from unittest.mock import MagicMock


class TestExtractAttachments:
    def test_extracts_pdf_attachment(self):
        from orchestrator.services.gmail_service import GmailService
        service = GmailService.__new__(GmailService)
        payload = {
            "parts": [
                {"filename": "invoice.pdf", "mimeType": "application/pdf",
                 "body": {"attachmentId": "att123", "size": 5000}},
                {"filename": "", "body": {}},  # no attachment
            ]
        }
        result = service._extract_attachments(payload)
        assert len(result) == 1
        assert result[0]["filename"] == "invoice.pdf"
        assert result[0]["attachmentId"] == "att123"

    def test_extracts_nested_attachments(self):
        from orchestrator.services.gmail_service import GmailService
        service = GmailService.__new__(GmailService)
        payload = {
            "parts": [{
                "filename": "",
                "body": {},
                "parts": [
                    {"filename": "nested.pdf", "mimeType": "application/pdf",
                     "body": {"attachmentId": "att456", "size": 3000}},
                ]
            }]
        }
        result = service._extract_attachments(payload)
        assert len(result) == 1
        assert result[0]["filename"] == "nested.pdf"

    def test_returns_empty_for_no_attachments(self):
        from orchestrator.services.gmail_service import GmailService
        service = GmailService.__new__(GmailService)
        payload = {"parts": [{"filename": "", "body": {}}]}
        result = service._extract_attachments(payload)
        assert result == []
