"""Email sending utilities (verification, password reset).

For development, emails are printed to stdout if SMTP is not configured.
For production, configure SMTP_* environment variables (NCP Cloud Outbound Mailer
or any SMTP server).
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import get_settings

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
    """Send an email via SMTP. Returns True on success, False on failure.

    In development (no SMTP configured), prints to stdout for debugging.
    """
    settings = get_settings()

    if not settings.smtp_host:
        logger.warning(
            "[EMAIL DEV MODE] Would send to %s\nSubject: %s\n---\n%s",
            to, subject, text_body or html_body,
        )
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to
        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to, e)
        return False


def send_verification_email(to: str, token: str) -> bool:
    settings = get_settings()
    verify_url = f"{settings.frontend_url}/auth/verify-email?token={token}"
    subject = "[English Master] 이메일 인증"
    html = f"""
    <h2>English Master에 오신 것을 환영합니다!</h2>
    <p>아래 링크를 클릭하여 이메일을 인증해주세요:</p>
    <p><a href="{verify_url}">이메일 인증하기</a></p>
    <p>이 링크는 24시간 동안 유효합니다.</p>
    <hr>
    <small>본인이 요청하지 않았다면 이 메일을 무시해주세요.</small>
    """
    text = f"English Master 이메일 인증\n\n다음 링크를 클릭하여 인증해주세요:\n{verify_url}\n\n24시간 내에 인증해주세요."
    return send_email(to, subject, html, text)


def send_password_reset_email(to: str, token: str) -> bool:
    settings = get_settings()
    reset_url = f"{settings.frontend_url}/auth/reset-password?token={token}"
    subject = "[English Master] 비밀번호 재설정"
    html = f"""
    <h2>비밀번호 재설정 요청</h2>
    <p>아래 링크를 클릭하여 비밀번호를 재설정해주세요:</p>
    <p><a href="{reset_url}">비밀번호 재설정</a></p>
    <p>이 링크는 1시간 동안 유효합니다.</p>
    <hr>
    <small>본인이 요청하지 않았다면 이 메일을 무시해주세요.</small>
    """
    text = f"비밀번호 재설정\n\n다음 링크를 클릭하여 재설정해주세요:\n{reset_url}\n\n1시간 내에 재설정해주세요."
    return send_email(to, subject, html, text)
