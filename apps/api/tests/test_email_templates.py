from __future__ import annotations

import inspect

import pytest

from app.transactional_email import templates


@pytest.mark.parametrize(
    ("kind", "headline", "button_label", "expiry"),
    [
        ("verification", "Verify your email", "Verify email", "24 hours"),
        ("password_reset", "Reset your password", "Reset password", "1 hour"),
    ],
)
def test_auth_email_has_branded_html_and_plain_text(
    kind: templates.AuthEmailKind,
    headline: str,
    button_label: str,
    expiry: str,
) -> None:
    action_url = f"https://app.rockyos.in/action?token={kind}-token"

    rendered = templates.render_auth_email(
        kind=kind,
        name="Ada Lovelace",
        action_url=action_url,
        expiry=expiry,
    )

    assert "<h1" in rendered.html
    assert headline in rendered.html
    assert headline in rendered.text
    assert button_label in rendered.html
    assert f'href="{action_url}"' in rendered.html
    assert f">{action_url}</a>" in rendered.html
    assert action_url in rendered.text
    assert f"This link expires in {expiry}." in rendered.html
    assert f"This link expires in {expiry}." in rendered.text
    for address in (
        "support@rockyos.in",
        "privacy@rockyos.in",
        "security@rockyos.in",
    ):
        assert address in rendered.html
        assert address in rendered.text
        assert f'href="mailto:{address}"' in rendered.html
    assert "Personal intelligence, carried forward." in rendered.html
    assert "Personal intelligence, carried forward." in rendered.text


def test_auth_template_has_no_environment_or_placeholder_urls() -> None:
    source = inspect.getsource(templates)

    assert "localhost" not in source
    assert "example.com" not in source
