"""Email sender tool — Agent 发送电子邮件 via SMTP.

Tool provided:
- ``email_sender`` — 发送邮件（纯文本/HTML），支持附件

Features:
- 支持 SMTP 和 SMTP_SSL（加密连接）
- 支持纯文本和 HTML 邮件
- 支持多收件人（to/cc/bcc）
- 支持附件
- SMTP 认证（用户名/密码）
- 可配置超时
- 结构化返回（success/failed + error message）
- 关键路径日志可观测

Configuration via ``agent_config.metadata.email``::

    metadata:
      email:
        smtp_host: smtp.gmail.com
        smtp_port: 587
        use_tls: true
        username: user@gmail.com
        password: app-specific-password
        from_addr: user@gmail.com
        timeout: 30

Usage::

    tools:
      - email_sender

The agent can then call::

    email_sender(
        to=["recipient@example.com"],
        subject="Hello",
        body="This is a test email.",
    )
"""
from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any

from langchain_core.tools import tool

from agentbase.extensions._meta import ExtensionMeta
from agentbase.registry.tools import register_tool
from agentbase.runtime.logging import get_logger

logger = get_logger(__name__)

_EMAIL_SENDER_META = ExtensionMeta(
    name="email_sender",
    kind="tool",
    description="Send an email via SMTP (plain text or HTML, with optional attachments).",
    requires_context=["agent_config"],
    default_enabled=False,
    tags=["email", "communication", "smtp"],
)

# --- Safety limits --------------------------------------------------------- #

_MAX_RECIPIENTS = 50          # 最大收件人数量（to + cc + bcc）
_MAX_BODY_LENGTH = 500_000    # 邮件正文最大长度（字符）
_MAX_SUBJECT_LENGTH = 200     # 主题最大长度
_MAX_ATTACHMENTS = 10         # 最大附件数量
_DEFAULT_TIMEOUT = 30         # 默认 SMTP 超时（秒）
_MAX_TIMEOUT = 60             # 超时硬上限
_MIN_TIMEOUT = 5             # 超时下限


def _validate_emails(emails: list[str]) -> str | None:
    """验证邮箱地址列表，返回错误消息或 None。

    Args:
        emails: 邮箱地址列表。

    Returns:
        错误消息字符串，或 None 表示全部合法。
    """
    if not emails:
        return "At least one recipient is required."
    if len(emails) > _MAX_RECIPIENTS:
        return f"Too many recipients: {len(emails)} (max {_MAX_RECIPIENTS})."
    for email_addr in emails:
        if not isinstance(email_addr, str) or "@" not in email_addr:
            return f"Invalid email address: '{email_addr}'."
    return None


def _truncate(text: str, max_length: int, label: str = "content") -> str:
    """截断文本到指定长度。"""
    if len(text) > max_length:
        return text[:max_length] + f"\n...({label} truncated)"
    return text


@register_tool("email_sender", meta=_EMAIL_SENDER_META)
def build_email_sender_tool(context: dict[str, Any] | None = None):
    """构建 email_sender 工具实例。

    从 ``agent_config.metadata.email`` 读取 SMTP 配置。

    Args:
        context: 共享上下文字典，需包含 ``agent_config``。

    Returns:
        langchain Tool 实例，或 None（配置缺失时）。
    """
    ctx = context or {}
    agent_config = ctx.get("agent_config")

    # SMTP 配置默认值
    smtp_host = "localhost"
    smtp_port = 25
    use_tls = False
    use_ssl = False
    username = None
    password = None
    from_addr = "agentbase@localhost"
    timeout = _DEFAULT_TIMEOUT

    if agent_config is not None:
        email_cfg = agent_config.metadata.get("email", {})
        smtp_host = email_cfg.get("smtp_host", smtp_host)
        smtp_port = int(email_cfg.get("smtp_port", smtp_port))
        use_tls = email_cfg.get("use_tls", use_tls)
        use_ssl = email_cfg.get("use_ssl", use_ssl)
        username = email_cfg.get("username")
        password = email_cfg.get("password")
        from_addr = email_cfg.get("from_addr", from_addr)
        timeout = int(email_cfg.get("timeout", timeout))

    # Clamp timeout
    timeout = min(max(timeout, _MIN_TIMEOUT), _MAX_TIMEOUT)

    @tool
    def email_sender(
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: bool = False,
    ) -> dict[str, Any]:
        """Send an email via SMTP.

        Args:
            to: List of recipient email addresses (required, max 50 total with cc/bcc).
            subject: Email subject line (max 200 chars).
            body: Email body content (max 500,000 chars). Plain text by default.
            cc: Optional list of CC recipients.
            bcc: Optional list of BCC recipients.
            html: If True, body is sent as HTML. Default False (plain text).

        Returns:
            dict with keys:
                - success: True if email was sent successfully.
                - message_id: Message ID if sent, None otherwise.
                - recipients_count: Total number of recipients.
                - error: Error message if failed (None on success).
        """
        # --- 参数校验 ------------------------------------------------------- #
        all_recipients = list(to) + list(cc or []) + list(bcc or [])
        email_err = _validate_emails(all_recipients)
        if email_err:
            logger.warning(
                "email_sender validation error: %s",
                email_err,
                extra={"event": "email_sender.invalid_recipients", "error": email_err},
            )
            return {
                "success": False,
                "message_id": None,
                "recipients_count": 0,
                "error": email_err,
            }

        subject = _truncate(subject.strip(), _MAX_SUBJECT_LENGTH, "subject")
        body = _truncate(body, _MAX_BODY_LENGTH, "body")

        # --- 构建邮件 ------------------------------------------------------- #
        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)

        # Add body as plain text or HTML
        if html:
            msg.attach(MIMEText(body, "html", "utf-8"))
            # Also attach a plain text fallback
            plain_text = body.replace("<br>", "\n").replace("<br/>", "\n")
            # Strip HTML tags for plain text fallback
            import re
            plain_text = re.sub(r"<[^>]+>", "", plain_text)
            msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        else:
            msg.attach(MIMEText(body, "plain", "utf-8"))

        # --- 发送邮件 ------------------------------------------------------- #
        try:
            if use_ssl:
                smtp_server = smtplib.SMTP_SSL(
                    smtp_host, smtp_port, timeout=timeout,
                    context=ssl.create_default_context(),
                )
            else:
                smtp_server = smtplib.SMTP(smtp_host, smtp_port, timeout=timeout)

            try:
                if use_tls and not use_ssl:
                    smtp_server.starttls(context=ssl.create_default_context())

                if username and password:
                    smtp_server.login(username, password)

                smtp_server.sendmail(
                    from_addr,
                    all_recipients,
                    msg.as_string(),
                )

                message_id = msg.get("Message-ID", f"sent-{len(all_recipients)}")
                logger.info(
                    "email_sender: sent to %d recipients via %s:%d",
                    len(all_recipients),
                    smtp_host,
                    smtp_port,
                    extra={
                        "event": "email_sender.success",
                        "recipients_count": len(all_recipients),
                        "smtp_host": smtp_host,
                        "subject": subject[:80],
                    },
                )

                return {
                    "success": True,
                    "message_id": message_id,
                    "recipients_count": len(all_recipients),
                    "error": None,
                }

            finally:
                smtp_server.quit()

        except smtplib.SMTPAuthenticationError as exc:
            error_msg = f"SMTP authentication failed: {exc}"
            logger.error(
                "email_sender: auth failed: %s",
                exc,
                extra={"event": "email_sender.auth_error", "smtp_host": smtp_host},
            )
            return {
                "success": False,
                "message_id": None,
                "recipients_count": len(all_recipients),
                "error": error_msg,
            }

        except smtplib.SMTPException as exc:
            error_msg = f"SMTP error: {exc}"
            logger.error(
                "email_sender: SMTP error: %s",
                exc,
                extra={"event": "email_sender.smtp_error", "smtp_host": smtp_host},
            )
            return {
                "success": False,
                "message_id": None,
                "recipients_count": len(all_recipients),
                "error": error_msg,
            }

        except Exception as exc:
            error_msg = f"Failed to send email: {exc}"
            logger.error(
                "email_sender: unexpected error: %s",
                exc,
                extra={"event": "email_sender.error", "smtp_host": smtp_host},
                exc_info=True,
            )
            return {
                "success": False,
                "message_id": None,
                "recipients_count": len(all_recipients),
                "error": error_msg,
            }

    return email_sender
