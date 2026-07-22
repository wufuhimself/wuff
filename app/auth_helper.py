#!/usr/bin/env python3
"""
Automated OAuth token management helper.
Handles auth flow, token exchange, and validation.
"""

import time
import sys
from typing import Optional

import requests

from .config import config
from .token_store import save_token, get_valid_token
from .yahoo_client import YahooToken, get_yahoo_auth_url, refresh_token as yahoo_refresh_token


def exchange_code_for_token(code: str) -> Optional[YahooToken]:
    """Exchange authorization code for access token."""
    try:
        payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': config.yahoo_redirect_uri,
            'client_id': config.yahoo_client_id,
            'client_secret': config.yahoo_client_secret,
        }
        response = requests.post(
            'https://api.login.yahoo.com/oauth2/get_token',
            data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        response.raise_for_status()

        token_data = response.json()
        token_data['expires_at'] = int(time.time()) + int(token_data.get('expires_in', 0))

        token = YahooToken(
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            expires_in=token_data['expires_in'],
            token_type=token_data['token_type'],
            expires_at=token_data['expires_at'],
        )

        save_token(token)
        print("✓ Token saved successfully")
        print(f"  Expires in: {token.expires_in} seconds")
        return token

    except requests.exceptions.RequestException as e:
        print(f"✗ Token exchange failed: {e}")
        if hasattr(e.response, 'json'):
            print(f"  Error: {e.response.json()}")
        return None


def validate_token_access() -> bool:
    """Test if token has Fantasy Sports access."""
    token = get_valid_token()
    if not token:
        print("✗ No valid token found")
        return False

    try:
        headers = {'Authorization': f'Bearer {token.access_token}'}
        response = requests.get(
            'https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1?format=json',
            headers=headers,
            timeout=5
        )
        response.raise_for_status()
        print("✓ Token has Fantasy Sports access")
        return True

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            error_data = e.response.json()
            desc = error_data.get('error', {}).get('description', '')
            if 'additional_authorization_required' in desc:
                print("✗ App not authorized for Fantasy Sports API")
                print("  Check Yahoo Developer console - app needs Fantasy Sports permission")
            else:
                print(f"✗ Authentication failed: {desc}")
        else:
            print(f"✗ API error: {e}")
        return False

    except Exception as e:
        print(f"✗ Validation failed: {e}")
        return False


def show_token_status():
    """Display current token status."""
    token = get_valid_token()
    if not token:
        print("No token saved")
        return

    expires_in = token.expires_at - int(time.time())
    print("Token Status:")
    print(f"  Access Token: {token.access_token[:30]}...")
    print(f"  Expires in: {expires_in} seconds ({expires_in // 60} minutes)")
    print(f"  Token Type: {token.token_type}")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python3 -m app.auth_helper <command> [args]")
        print("\nCommands:")
        print("  get-auth-url          - Print OAuth authorization URL")
        print("  exchange <code>       - Exchange auth code for token")
        print("  validate              - Test if token has Fantasy Sports access")
        print("  status                - Show current token status")
        print("  refresh               - Refresh token if expired")
        sys.exit(1)

    command = sys.argv[1]

    if command == 'get-auth-url':
        url = get_yahoo_auth_url()
        print(f"Authorization URL:\n{url}")

    elif command == 'exchange' and len(sys.argv) > 2:
        code = sys.argv[2]
        token = exchange_code_for_token(code)
        if token and validate_token_access():
            print("\n✓ Setup complete!")

    elif command == 'validate':
        validate_token_access()

    elif command == 'status':
        show_token_status()

    elif command == 'refresh':
        token = get_valid_token()
        if token and token.refresh_token:
            new_token = yahoo_refresh_token(token.refresh_token)
            save_token(new_token)
            print("✓ Token refreshed")
        else:
            print("✗ Cannot refresh - no token or refresh_token")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == '__main__':
    main()
