import ssl
import tempfile
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .config import config
from .yahoo_client import get_yahoo_auth_url


DEFAULT_PORT = 3000


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    server_version = 'YahooOAuthCallback/1.0'

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != '/oauth/callback':
            self.send_response(404)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h1>Not found</h1>')
            return

        query = urllib.parse.parse_qs(parsed.query)
        code = query.get('code', [None])[0]
        error = query.get('error', [None])[0]

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()

        if error:
            self.wfile.write(f'<h1>Yahoo OAuth failed</h1><p>{error}</p>'.encode('utf-8'))
            print('OAuth error:', error)
        elif code:
            self.wfile.write(b'<h1>Yahoo OAuth complete</h1><p>You can close this window.</p>')
            print('Authorization code:', code)
            print('Use it with: python -m app.cli token <code>')
        else:
            self.wfile.write(b'<h1>No code returned</h1>')

        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format: str, *args: object) -> None:  # pylint: disable=redefined-builtin,unused-argument
        return


def generate_self_signed_cert(temp_dir: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'localhost'),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path = temp_dir / 'localhost.key'
    cert_path = temp_dir / 'localhost.crt'

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return key_path, cert_path


def load_cert_paths() -> tuple[Path, Path, Optional[tempfile.TemporaryDirectory]]:
    if config.yahoo_ssl_key_path and config.yahoo_ssl_cert_path:
        return (
            Path(config.yahoo_ssl_key_path).expanduser(),
            Path(config.yahoo_ssl_cert_path).expanduser(),
            None,
        )

    temp_dir = tempfile.TemporaryDirectory(prefix='yahoo_oauth_cert_')  # pylint: disable=consider-using-with
    path = Path(temp_dir.name)
    key_path, cert_path = generate_self_signed_cert(path)
    return key_path, cert_path, temp_dir


def run_yahoo_oauth_server(port: int = DEFAULT_PORT) -> None:
    key_path, cert_path, temp_dir = load_cert_paths()
    server_address = ('localhost', port)
    httpd = HTTPServer(server_address, OAuthCallbackHandler)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

    auth_url = get_yahoo_auth_url()
    print(f'HTTPS callback server listening at https://localhost:{port}/oauth/callback')
    print('Opening Yahoo authorization URL in your browser...')
    webbrowser.open(auth_url, new=2, autoraise=True)
    print('If your browser does not open automatically, visit:')
    print(auth_url)

    try:
        httpd.handle_request()
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
