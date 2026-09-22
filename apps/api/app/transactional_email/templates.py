"""Shared, email-client-safe templates for Rocky auth messages."""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Literal

from app.core.contact import PRIVACY_EMAIL, SECURITY_EMAIL, SUPPORT_EMAIL

AuthEmailKind = Literal["verification", "password_reset"]


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    html: str
    text: str


def render_auth_email(
    *,
    kind: AuthEmailKind,
    name: str,
    action_url: str,
    expiry: str,
) -> RenderedEmail:
    """Render a branded auth email without changing its caller-owned URL."""
    if kind == "verification":
        headline = "Verify your email"
        intro = (
            "You’re almost there. Confirm your email to finish setting up "
            "your Rocky workspace."
        )
        button_label = "Verify email"
        security_copy = (
            f"This link expires in {expiry}. If you didn’t create a Rocky "
            "account, you can safely ignore this email."
        )
    else:
        headline = "Reset your password"
        intro = "We received a request to reset your Rocky OS password."
        button_label = "Reset password"
        security_copy = (
            f"This link expires in {expiry}. If you didn’t request a password "
            "reset, you can safely ignore this email."
        )

    safe_name = html.escape(name)
    safe_url = html.escape(action_url, quote=True)
    safe_headline = html.escape(headline)
    safe_intro = html.escape(intro)
    safe_button_label = html.escape(button_label)
    safe_security_copy = html.escape(security_copy)

    html_body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <title>{safe_headline} | Rocky OS</title>
</head>
<body style="margin:0;padding:0;background-color:#efeee9;color:#171719;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{safe_intro}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#efeee9" style="width:100%;background-color:#efeee9;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#171719" style="width:100%;max-width:600px;background-color:#171719;border:1px solid #303034;border-radius:12px;overflow:hidden;">
          <tr>
            <td style="padding:28px 40px;border-bottom:1px solid #303034;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#f2f1ed;font-size:16px;font-weight:700;letter-spacing:-0.2px;">Rocky OS</td>
          </tr>
          <tr>
            <td style="padding:52px 40px 46px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#f2f1ed;">
              <h1 style="margin:0 0 28px;color:#f2f1ed;font-size:32px;line-height:1.2;font-weight:600;letter-spacing:-0.8px;">{safe_headline}</h1>
              <p style="margin:0 0 14px;color:#d4d2cd;font-size:16px;line-height:1.6;">Hi {safe_name},</p>
              <p style="margin:0 0 32px;color:#aaa8a3;font-size:16px;line-height:1.65;">{safe_intro}</p>
              <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td bgcolor="#ebe9e3" style="border-radius:7px;">
                    <a href="{safe_url}" aria-label="{safe_button_label}" style="display:inline-block;padding:15px 24px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#171719;font-size:15px;font-weight:700;line-height:1;text-decoration:none;border-radius:7px;">{safe_button_label}</a>
                  </td>
                </tr>
              </table>
              <p style="margin:30px 0 0;padding-top:24px;border-top:1px solid #303034;color:#898781;font-size:13px;line-height:1.65;">{safe_security_copy}</p>
              <p style="margin:24px 0 8px;color:#6f6d68;font-size:12px;line-height:1.5;">If the button doesn’t work, copy and paste this link into your browser:</p>
              <p style="margin:0;word-break:break-all;color:#aaa5d1;font-size:12px;line-height:1.6;"><a href="{safe_url}" style="color:#aaa5d1;text-decoration:underline;">{safe_url}</a></p>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 40px;background-color:#111113;border-top:1px solid #303034;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
              <p style="margin:0 0 5px;color:#d4d2cd;font-size:13px;font-weight:700;">Rocky OS</p>
              <p style="margin:0 0 18px;color:#77756f;font-size:12px;line-height:1.5;">Personal intelligence, carried forward.</p>
              <p style="margin:0;color:#77756f;font-size:11px;line-height:1.8;"><a href="mailto:{SUPPORT_EMAIL}" style="color:#aaa5d1;text-decoration:none;">{SUPPORT_EMAIL}</a><br><a href="mailto:{PRIVACY_EMAIL}" style="color:#aaa5d1;text-decoration:none;">{PRIVACY_EMAIL}</a><br><a href="mailto:{SECURITY_EMAIL}" style="color:#aaa5d1;text-decoration:none;">{SECURITY_EMAIL}</a></p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    text_body = f"""Rocky OS

{headline}

Hi {name},

{intro}

{button_label}:
{action_url}

{security_copy}

If the button is unavailable, copy and paste the URL above into your browser.

Rocky OS
Personal intelligence, carried forward.
Support: {SUPPORT_EMAIL}
Privacy: {PRIVACY_EMAIL}
Security: {SECURITY_EMAIL}
"""
    return RenderedEmail(html=html_body, text=text_body)


__all__ = ["AuthEmailKind", "RenderedEmail", "render_auth_email"]
