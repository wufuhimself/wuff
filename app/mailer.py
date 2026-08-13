"""Transactional email sending (magic-link login today).

Backed by Resend's HTTP API. Without RESEND_API_KEY set, falls back to
printing the email to stdout instead of sending — lets magic-link login be
built and exercised locally before a Resend account exists. Real deploys
MUST set RESEND_API_KEY or every login silently no-ops for the user (they
never receive anything, only the server log does).
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

RESEND_API_URL = 'https://api.resend.com/emails'
# Resend's shared dev sender — works with zero domain setup. Swap for a
# verified from-address on your own domain once one exists (set
# WUFF_MAIL_FROM); until then this is fine for real sends too, Resend just
# stamps "via resend.dev" on delivery.
DEFAULT_FROM = 'wuff <onboarding@resend.dev>'


def send_magic_link(to_email: str, login_url: str) -> None:
    subject = 'Your wuff login link'
    text_body = (
        f'Click to log in to wuff (link expires in 15 minutes):\n\n{login_url}\n\n'
        "If you didn't request this, ignore this email."
    )
    html_body = (
        '<p>Click below to log in to wuff. This link expires in 15 minutes.</p>'
        f'<p><a href="{login_url}">Log in to wuff</a></p>'
        "<p style=\"color:#666;font-size:0.9em\">If you didn't request this, ignore this email.</p>"
    )
    api_key = os.environ.get('RESEND_API_KEY')
    if not api_key:
        # Dev fallback: no account needed to exercise the login flow locally.
        logger.warning('RESEND_API_KEY not set — printing magic link instead of emailing it')
        print(f'\n[dev magic link] {to_email} -> {login_url}\n')
        return

    from_addr = os.environ.get('WUFF_MAIL_FROM', DEFAULT_FROM)
    response = requests.post(
        RESEND_API_URL,
        headers={'Authorization': f'Bearer {api_key}'},
        json={
            'from': from_addr,
            'to': [to_email],
            'subject': subject,
            'html': html_body,
            'text': text_body,
        },
        timeout=10,
    )
    if response.status_code >= 400:
        logger.error('Resend send failed (%s): %s', response.status_code, response.text)
        raise RuntimeError(f'Failed to send login email: {response.status_code}')
