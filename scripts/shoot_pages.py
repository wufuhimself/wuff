#!/usr/bin/env python3
"""Screenshot wuff pages through the real Flask app, for visual review.

Serves the app on a local port and drives headless Chrome over the DevTools
protocol with real device metrics, so mobile widths honour the viewport meta
and lay out the way a phone does.

    python3 scripts/shoot_pages.py                    # default page set
    python3 scripts/shoot_pages.py / /leagues         # specific paths
    python3 scripts/shoot_pages.py --width 390 /      # one viewport
    python3 scripts/shoot_pages.py --logged-out /     # the welcome page
    python3 scripts/shoot_pages.py --full /standings  # full-page, not viewport

Prints an OVERFLOW line for any page whose document is wider than the
viewport (the measurable half of "does this look right"), then writes PNGs
to --out. LOOK AT THE PNGS -- the overflow check catches one failure mode,
not baseline drift, wrapping, or clipped text.

⚠️ Do NOT go back to `chrome --headless --window-size=W,H` against a file://
copy of the page. That ignores the viewport meta and lays out at a wider
viewport than asked for: a 390px request rendered at 485px and made the
keeper-impact cards look clipped on mobile when the real page is fine. This
script was rewritten onto CDP after that false alarm.
"""
import argparse
import asyncio
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
# Desktop, then the phone width where .brand-row wraps and the nav collapses.
DEFAULT_WIDTHS = (1100, 390)
DEFAULT_PATHS = ('/', '/keepers-board', '/leagues', '/league/{slug}/matchups')


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _serve(logged_out: bool):
    """Run the real app on a local port. Returns (base_url, slug, cookie)."""
    tmp_db = Path(tempfile.mkdtemp(prefix='wuff-shots-')) / 'shots.db'
    real_db = ROOT / 'data' / 'wuff.db'
    if real_db.exists():
        shutil.copyfile(real_db, tmp_db)
    os.environ['DATABASE_URL'] = f'sqlite:///{tmp_db}'
    os.environ['WUFF_DISABLE_SCHEDULER'] = '1'

    from app.db import SessionLocal, init_db  # pylint: disable=import-outside-toplevel
    from app.league_registry import load_leagues  # pylint: disable=import-outside-toplevel
    from app.membership import grant_league  # pylint: disable=import-outside-toplevel
    from app.models import User  # pylint: disable=import-outside-toplevel
    from app.web import app  # pylint: disable=import-outside-toplevel

    init_db()
    leagues = load_leagues()
    slug = next((s for s, lg in leagues.items() if lg.platform == 'yahoo'),
                next(iter(leagues), None))

    cookie = None
    if not logged_out:
        with SessionLocal() as session:
            user = User(email='shots@example.com')
            session.add(user)
            session.commit()
            user_id = user.id
        grant_league('shots@example.com', slug, make_default=True)
        client = app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
        # Reuse the signed session cookie in the browser: the shots must show
        # a logged-in page, and every route but login/logout is gated.
        response = client.get('/')
        for header in response.headers.getlist('Set-Cookie'):
            if header.startswith('session='):
                cookie = header.split('session=', 1)[1].split(';', 1)[0]

    port = _free_port()
    threading.Thread(
        target=lambda: app.run(host='127.0.0.1', port=port, debug=False,
                               use_reloader=False, threaded=True),
        daemon=True).start()
    for _ in range(100):
        try:
            urllib.request.urlopen(  # pylint: disable=consider-using-with
                f'http://127.0.0.1:{port}/login', timeout=1)
            break
        except Exception:  # pylint: disable=broad-exception-caught
            time.sleep(0.1)
    return f'http://127.0.0.1:{port}', slug, cookie


class Browser:
    """Minimal CDP driver. websockets is already a dependency (via langgraph)."""

    def __init__(self):
        self.profile = tempfile.mkdtemp(prefix='wuff-chrome-')
        self.port = _free_port()
        self.proc = subprocess.Popen(  # pylint: disable=consider-using-with
            [CHROME, '--headless', '--disable-gpu', f'--remote-debugging-port={self.port}',
             '--no-first-run', '--no-default-browser-check', f'--user-data-dir={self.profile}',
             'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws_url = None
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(  # pylint: disable=consider-using-with
                    f'http://127.0.0.1:{self.port}/json'))
                self.ws_url = next(t['webSocketDebuggerUrl'] for t in tabs if t['type'] == 'page')
                break
            except Exception:  # pylint: disable=broad-exception-caught
                time.sleep(0.1)
        if not self.ws_url:
            raise RuntimeError('Could not reach Chrome over CDP')

    def close(self):
        self.proc.terminate()
        shutil.rmtree(self.profile, ignore_errors=True)


async def _shoot_all(base_url, paths, widths, height, cookie, out_dir, full):
    import websockets  # pylint: disable=import-outside-toplevel

    browser = Browser()
    results = []
    try:
        async with websockets.connect(browser.ws_url, max_size=None) as sock:
            counter = {'n': 0}

            async def cmd(method, params=None):
                counter['n'] += 1
                msg_id = counter['n']
                await sock.send(json.dumps({'id': msg_id, 'method': method,
                                            'params': params or {}}))
                while True:
                    message = json.loads(await sock.recv())
                    if message.get('id') == msg_id:
                        return message.get('result', {})

            await cmd('Page.enable')
            await cmd('Network.enable')
            if cookie:
                await cmd('Network.setCookie', {'name': 'session', 'value': cookie,
                                                'domain': '127.0.0.1', 'path': '/'})
            for path in paths:
                for width in widths:
                    # mobile=True is the whole point: it makes Chrome honour the
                    # viewport meta instead of laying out at some wider width.
                    await cmd('Emulation.setDeviceMetricsOverride',
                              {'width': width, 'height': height, 'deviceScaleFactor': 2,
                               'mobile': width < 700})
                    await cmd('Page.navigate', {'url': base_url + path})
                    await asyncio.sleep(1.6)
                    probe = await cmd('Runtime.evaluate', {
                        'expression': "JSON.stringify({d:document.documentElement.scrollWidth,"
                                      "v:innerWidth,t:document.title})",
                        'returnByValue': True})
                    info = json.loads(probe['result']['value'])
                    name = re.sub(r'\W+', '_', path).strip('_') or 'root'
                    shot = out_dir / f'{name}_{width}.png'
                    params = {'format': 'png'}
                    if full:
                        params['captureBeyondViewport'] = True
                    data = await cmd('Page.captureScreenshot', params)
                    shot.write_bytes(base64.b64decode(data['data']))
                    overflow = info['d'] > info['v'] + 1
                    flag = f"  OVERFLOW doc={info['d']} viewport={info['v']}" if overflow else ''
                    print(f'  ok {path}  {width}px  ->  {shot}{flag}')
                    results.append(overflow)
    finally:
        browser.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('paths', nargs='*')
    parser.add_argument('--width', type=int, action='append', dest='widths')
    parser.add_argument('--height', type=int, default=900)
    parser.add_argument('--logged-out', action='store_true')
    parser.add_argument('--full', action='store_true', help='full page, not just the viewport')
    parser.add_argument('--out')
    args = parser.parse_args()

    if not Path(CHROME).exists():
        print(f'Chrome not found at {CHROME}', file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix='wuff-shots-'))
    out_dir.mkdir(parents=True, exist_ok=True)
    widths = args.widths or list(DEFAULT_WIDTHS)

    base_url, slug, cookie = _serve(args.logged_out)
    paths = [p.format(slug=slug) for p in (args.paths or DEFAULT_PATHS)]

    overflows = asyncio.run(_shoot_all(base_url, paths, widths, args.height,
                                       cookie, out_dir, args.full))
    print(f'\n{len(paths)} page(s) x {len(widths)} width(s) -> {out_dir}')
    if any(overflows):
        print('Horizontal overflow on at least one page -- see the OVERFLOW lines.')
    print('Now LOOK at the PNGs: overflow is one failure mode, not all of them.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
