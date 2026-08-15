"""Tests for email_sender tool — covers validation, SMTP mocking, edge cases.

Tests verify:
1. Tool registration and metadata
2. Email address validation
3. Subject/body truncation
4. SMTP connection and send (mocked)
5. SMTP SSL mode
6. SMTP TLS mode
7. Authentication failure handling
8. HTML mode
9. Multiple recipients (to/cc/bcc)
10. Body truncation
"""
from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestEmailSenderRegistration:
    def test_registered_in_tool_registry(self):
        import agentbase.extensions.tools.email_sender  # noqa: F401
        from agentbase.registry.tools import tool_registry

        assert tool_registry.has("email_sender")

    def test_meta_name(self):
        from agentbase.extensions.tools.email_sender import _EMAIL_SENDER_META

        assert _EMAIL_SENDER_META.name == "email_sender"
        assert _EMAIL_SENDER_META.kind == "tool"

    def test_meta_default_disabled(self):
        from agentbase.extensions.tools.email_sender import _EMAIL_SENDER_META

        assert _EMAIL_SENDER_META.default_enabled is False


# ---------------------------------------------------------------------------
# Validation helper tests
# ---------------------------------------------------------------------------


class TestValidateEmails:
    def test_valid_emails(self):
        from agentbase.extensions.tools.email_sender import _validate_emails

        assert _validate_emails(["alice@example.com", "bob@test.org"]) is None

    def test_empty_list(self):
        from agentbase.extensions.tools.email_sender import _validate_emails

        result = _validate_emails([])
        assert result is not None
        assert "required" in result.lower()

    def test_invalid_email_no_at(self):
        from agentbase.extensions.tools.email_sender import _validate_emails

        result = _validate_emails(["invalid-email"])
        assert result is not None
        assert "invalid" in result.lower()

    def test_too_many_recipients(self):
        from agentbase.extensions.tools.email_sender import _validate_emails

        emails = [f"user{i}@example.com" for i in range(51)]
        result = _validate_emails(emails)
        assert result is not None
        assert "too many" in result.lower()


# ---------------------------------------------------------------------------
# Truncation helper tests
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_no_truncation(self):
        from agentbase.extensions.tools.email_sender import _truncate

        result = _truncate("short text", 100)
        assert result == "short text"

    def test_truncation(self):
        from agentbase.extensions.tools.email_sender import _truncate

        long_text = "a" * 200
        result = _truncate(long_text, 100, "body")
        assert len(result) < 200
        assert "truncated" in result


# ---------------------------------------------------------------------------
# Build and tool behavior tests (with mocked SMTP)
# ---------------------------------------------------------------------------


class TestEmailSenderTool:
    """Test the email_sender tool with mocked SMTP."""

    def _create_tool(self, smtp_config=None):
        """Create the email_sender tool with config."""
        from agentbase.extensions.tools.email_sender import build_email_sender_tool

        mock_config = MagicMock()
        mock_config.metadata = smtp_config or {
            "email": {
                "smtp_host": "smtp.test.com",
                "smtp_port": 587,
                "use_tls": True,
                "username": "test@test.com",
                "password": "password",
                "from_addr": "test@test.com",
            }
        }

        return build_email_sender_tool(context={"agent_config": mock_config})

    def test_build_without_config(self):
        """Building without agent_config should use defaults."""
        from agentbase.extensions.tools.email_sender import build_email_sender_tool

        tool_instance = build_email_sender_tool(context={})
        assert tool_instance is not None

    def test_build_with_none_context(self):
        from agentbase.extensions.tools.email_sender import build_email_sender_tool

        tool_instance = build_email_sender_tool(context=None)
        assert tool_instance is not None

    @patch("smtplib.SMTP")
    def test_send_plain_text_email(self, mock_smtp_class):
        mock_smtp = MagicMock()
        mock_smtp_class.return_value = mock_smtp

        email_tool = self._create_tool()
        result = email_tool.invoke({
            "to": ["recipient@example.com"],
            "subject": "Test Subject",
            "body": "Hello, this is a test.",
        })

        assert result["success"] is True
        assert result["recipients_count"] == 1
        assert result["error"] is None
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once()
        mock_smtp.sendmail.assert_called_once()
        mock_smtp.quit.assert_called_once()

    @patch("smtplib.SMTP")
    def test_send_html_email(self, mock_smtp_class):
        mock_smtp = MagicMock()
        mock_smtp_class.return_value = mock_smtp

        email_tool = self._create_tool()
        result = email_tool.invoke({
            "to": ["recipient@example.com"],
            "subject": "HTML Test",
            "body": "<h1>Hello</h1><p>This is HTML.</p>",
            "html": True,
        })

        assert result["success"] is True
        # Check that sendmail was called with HTML content
        sent_data = mock_smtp.sendmail.call_args[0][2]
        assert "text/html" in sent_data

    @patch("smtplib.SMTP")
    def test_send_with_cc_and_bcc(self, mock_smtp_class):
        mock_smtp = MagicMock()
        mock_smtp_class.return_value = mock_smtp

        email_tool = self._create_tool()
        result = email_tool.invoke({
            "to": ["to1@example.com", "to2@example.com"],
            "cc": ["cc1@example.com"],
            "bcc": ["bcc1@example.com"],
            "subject": "Multi recipients",
            "body": "Test",
        })

        assert result["success"] is True
        assert result["recipients_count"] == 4  # 2 to + 1 cc + 1 bcc

        # Verify all recipients were passed to sendmail
        recipients = mock_smtp.sendmail.call_args[0][1]
        assert len(recipients) == 4

    @patch("smtplib.SMTP_SSL")
    def test_send_via_ssl(self, mock_smtp_ssl_class):
        mock_smtp = MagicMock()
        mock_smtp_ssl_class.return_value = mock_smtp

        email_tool = self._create_tool(smtp_config={
            "email": {
                "smtp_host": "smtp.test.com",
                "smtp_port": 465,
                "use_ssl": True,
                "username": "test@test.com",
                "password": "password",
                "from_addr": "test@test.com",
            }
        })
        result = email_tool.invoke({
            "to": ["recipient@example.com"],
            "subject": "SSL Test",
            "body": "Via SSL",
        })

        assert result["success"] is True
        mock_smtp.login.assert_called_once()
        mock_smtp.sendmail.assert_called_once()
        # starttls should NOT be called with SSL
        mock_smtp.starttls.assert_not_called()

    @patch("smtplib.SMTP")
    def test_auth_failure(self, mock_smtp_class):
        mock_smtp = MagicMock()
        mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(
            code=535, msg="Authentication failed"
        )
        mock_smtp_class.return_value = mock_smtp

        email_tool = self._create_tool()
        result = email_tool.invoke({
            "to": ["recipient@example.com"],
            "subject": "Auth fail test",
            "body": "Test",
        })

        assert result["success"] is False
        assert "authentication" in result["error"].lower()

    @patch("smtplib.SMTP")
    def test_smtp_error(self, mock_smtp_class):
        mock_smtp = MagicMock()
        mock_smtp.sendmail.side_effect = smtplib.SMTPException("Connection refused")
        mock_smtp_class.return_value = mock_smtp

        email_tool = self._create_tool()
        result = email_tool.invoke({
            "to": ["recipient@example.com"],
            "subject": "SMTP error test",
            "body": "Test",
        })

        assert result["success"] is False
        assert "smtp" in result["error"].lower()

    @patch("smtplib.SMTP")
    def test_unexpected_error(self, mock_smtp_class):
        mock_smtp = MagicMock()
        mock_smtp.sendmail.side_effect = RuntimeError("Unexpected")
        mock_smtp_class.return_value = mock_smtp

        email_tool = self._create_tool()
        result = email_tool.invoke({
            "to": ["recipient@example.com"],
            "subject": "Error test",
            "body": "Test",
        })

        assert result["success"] is False
        assert "failed" in result["error"].lower()

    def test_invalid_recipients(self):
        email_tool = self._create_tool()
        result = email_tool.invoke({
            "to": ["invalid-email"],
            "subject": "Test",
            "body": "Test",
        })

        assert result["success"] is False
        assert "invalid" in result["error"].lower()

    def test_empty_recipients(self):
        email_tool = self._create_tool()
        result = email_tool.invoke({
            "to": [],
            "subject": "Test",
            "body": "Test",
        })

        assert result["success"] is False
        assert "required" in result["error"].lower()

    @patch("smtplib.SMTP")
    def test_long_subject_truncated(self, mock_smtp_class):
        mock_smtp = MagicMock()
        mock_smtp_class.return_value = mock_smtp

        email_tool = self._create_tool()
        long_subject = "A" * 300
        result = email_tool.invoke({
            "to": ["recipient@example.com"],
            "subject": long_subject,
            "body": "Test",
        })

        assert result["success"] is True
        sent_data = mock_smtp.sendmail.call_args[0][2]
        assert "truncated" in sent_data

    @patch("smtplib.SMTP")
    def test_no_auth_when_no_credentials(self, mock_smtp_class):
        """When username/password not set, should skip login."""
        mock_smtp = MagicMock()
        mock_smtp_class.return_value = mock_smtp

        email_tool = self._create_tool(smtp_config={
            "email": {
                "smtp_host": "localhost",
                "smtp_port": 25,
                "use_tls": False,
                "from_addr": "test@localhost",
            }
        })
        result = email_tool.invoke({
            "to": ["recipient@example.com"],
            "subject": "No auth test",
            "body": "Test",
        })

        assert result["success"] is True
        mock_smtp.login.assert_not_called()
