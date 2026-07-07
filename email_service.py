"""이메일 발송 (표준 라이브러리 smtplib 기반).

외부 의존성 없이 어떤 SMTP 계정(Gmail 앱 비밀번호, 회사 메일 등)이든 붙일 수 있다.
설정 값은 DB(app_settings)에 저장되며 관리자 도구에서 편집한다 — 발송 주체는
우리 회사가 아니라 이 서비스를 운영하는 사람(어드민)이다.
"""
import ssl
import smtplib
import logging
from email.message import EmailMessage
from email.utils import formataddr


def send_email(settings, to_email, subject, text_body, html_body=None):
    """SMTP로 메일을 보낸다.
    settings: database.get_email_settings() 결과 dict
    반환: (성공여부: bool, 에러메시지 또는 None)
    """
    if not settings.get("enabled"):
        return False, "email_disabled"
    host = (settings.get("smtp_host") or "").strip()
    if not host:
        return False, "smtp_not_configured"

    from_addr = (settings.get("email_from") or settings.get("smtp_user") or "").strip()
    if not from_addr:
        return False, "from_address_missing"
    from_name = settings.get("email_from_name") or "English Master"

    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    port = int(settings.get("smtp_port") or 587)
    security = (settings.get("smtp_security") or "tls").lower()
    user = settings.get("smtp_user") or ""
    password = settings.get("smtp_password") or ""

    try:
        if security == "ssl":
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=15, context=ctx) as s:
                if user:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.ehlo()
                if security == "tls":
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                if user:
                    s.login(user, password)
                s.send_message(msg)
        return True, None
    except Exception as e:  # noqa: BLE001 - 발송 실패를 호출부에 문자열로 전달
        logging.warning(f"[email] send failed to {to_email}: {e}")
        return False, str(e)


def verification_email_bodies(link, app_name="English Master"):
    """인증 메일의 (제목, 텍스트본문, HTML본문)을 만든다."""
    subject = f"[{app_name}] 이메일 인증을 완료해 주세요"
    text = (
        f"{app_name} 가입을 환영합니다!\n\n"
        f"아래 링크를 눌러 이메일 인증을 완료하세요:\n{link}\n\n"
        f"본인이 요청하지 않았다면 이 메일을 무시하세요."
    )
    html = f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1e293b;">
  <h2 style="margin:0 0 8px;">{app_name} 이메일 인증</h2>
  <p style="color:#475569;line-height:1.6;">가입을 환영합니다! 아래 버튼을 눌러 이메일 인증을 완료하세요.</p>
  <p style="margin:24px 0;">
    <a href="{link}" style="display:inline-block;background:#6366f1;color:#fff;text-decoration:none;padding:12px 22px;border-radius:10px;font-weight:600;">이메일 인증하기</a>
  </p>
  <p style="color:#94a3b8;font-size:13px;line-height:1.6;">버튼이 동작하지 않으면 아래 주소를 브라우저에 붙여넣으세요:<br><span style="word-break:break-all;">{link}</span></p>
  <p style="color:#94a3b8;font-size:12px;margin-top:24px;">본인이 요청하지 않았다면 이 메일을 무시하세요.</p>
</div>"""
    return subject, text, html


def password_reset_email_bodies(link, app_name="English Master"):
    """비밀번호 재설정 메일의 (제목, 텍스트본문, HTML본문)을 만든다."""
    subject = f"[{app_name}] 비밀번호 재설정 안내"
    text = (
        f"{app_name} 비밀번호 재설정을 요청하셨습니다.\n\n"
        f"아래 링크를 눌러 새 비밀번호를 설정하세요(1시간 내 유효):\n{link}\n\n"
        f"본인이 요청하지 않았다면 이 메일을 무시하세요. 비밀번호는 변경되지 않습니다."
    )
    html = f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1e293b;">
  <h2 style="margin:0 0 8px;">{app_name} 비밀번호 재설정</h2>
  <p style="color:#475569;line-height:1.6;">아래 버튼을 눌러 새 비밀번호를 설정하세요. 이 링크는 <b>1시간</b> 동안만 유효합니다.</p>
  <p style="margin:24px 0;">
    <a href="{link}" style="display:inline-block;background:#6366f1;color:#fff;text-decoration:none;padding:12px 22px;border-radius:10px;font-weight:600;">비밀번호 재설정</a>
  </p>
  <p style="color:#94a3b8;font-size:13px;line-height:1.6;">버튼이 동작하지 않으면 아래 주소를 브라우저에 붙여넣으세요:<br><span style="word-break:break-all;">{link}</span></p>
  <p style="color:#94a3b8;font-size:12px;margin-top:24px;">본인이 요청하지 않았다면 이 메일을 무시하세요. 비밀번호는 변경되지 않습니다.</p>
</div>"""
    return subject, text, html
