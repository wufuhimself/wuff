#!/usr/bin/env python3
"""Assert Scouting degrades honestly when no LLM is reachable.

Scouting shipped 2026-08-19 against a *local* Ollama. Production (Railway)
runs none and sets no OLLAMA_BASE_URL, so every question asked on the live
app died inside ChatOllama.invoke() with a connection error -- a 500 on a
feature still advertised in the nav. The gate (2026-08-21) renders an offline
state instead. This checks it from the outside, through real requests against
a throwaway database, because both failure modes are silent-ish: a gate that
never lifts leaves the feature dead everywhere, and a gate that never engages
puts the 500 back.

    python3 scripts/check_scouting_gate.py

Exits non-zero on any failed expectation. The offline half runs anywhere (it
points the probe at a dead port); the reachable half needs a real local Ollama
carrying LLM_MODEL and is skipped, not failed, without one.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP_DB = Path(tempfile.mkdtemp(prefix='wuff-scouting-')) / 'gate.db'
# Must be set before app.db is imported anywhere -- the engine is module-level.
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP_DB}'
os.environ['WUFF_DISABLE_SCHEDULER'] = '1'

# pylint: disable=wrong-import-position
from app import agent_reasoning  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.league_registry import load_leagues  # noqa: E402
from app.membership import grant_league  # noqa: E402
from app.models import User  # noqa: E402
from app.web import app  # noqa: E402

DEAD_PORT_URL = 'http://127.0.0.1:59999'

FAILURES: list = []
SKIPPED: list = []


def check(label: str, expected, actual) -> None:
    if expected == actual:
        print(f'  ok   {label}')
        return
    FAILURES.append(label)
    print(f'  FAIL {label}')
    print(f'       expected: {expected!r}')
    print(f'       actual:   {actual!r}')


def point_probe_at(url: str) -> bool:
    """Repoint the Ollama probe and re-read it, cache bypassed."""
    agent_reasoning.OLLAMA_BASE_URL = url
    return agent_reasoning.llm_available(force=True)


def main() -> int:  # pylint: disable=too-many-statements
    init_db()

    slug = next((s for s, league in load_leagues().items() if league.platform == 'yahoo'), None)
    if slug is None:
        print('No Yahoo league in data/config/leagues.json -- run `python3 -m app leagues-init` first.')
        return 1

    with SessionLocal() as session:
        user = User(email='owner@example.com')
        session.add(user)
        session.commit()
        user_id = user.id
    grant_league('owner@example.com', slug, make_default=True)

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True

    real_url = agent_reasoning.OLLAMA_BASE_URL
    ollama_running = point_probe_at(real_url)

    print('\nthe probe')
    check('a dead port reads as unavailable', False, point_probe_at(DEAD_PORT_URL))
    if ollama_running:
        check('a real local Ollama carrying the model reads as available', True,
              point_probe_at(real_url))
        # Reachability alone is not the question: an Ollama that never pulled
        # the model fails at invoke() exactly like an absent one.
        real_model = agent_reasoning.LLM_MODEL
        agent_reasoning.LLM_MODEL = 'not-a-real-model:99b'
        check('a reachable server missing the model reads as unavailable', False,
              point_probe_at(real_url))
        agent_reasoning.LLM_MODEL = real_model

        print('\nthe cache')
        point_probe_at(real_url)
        agent_reasoning.OLLAMA_BASE_URL = DEAD_PORT_URL  # no force -- cache still stands
        check('an answer inside the TTL is served from cache', True, agent_reasoning.llm_available())
        check('force=True re-probes', False, agent_reasoning.llm_available(force=True))

        print('\nthe page, model reachable')
        point_probe_at(real_url)
        body = client.get(f'/league/{slug}/scouting').get_data(as_text=True)
        check('question form rendered', True, 'id="ask-form"' in body)
        # The rendered ELEMENT, not the class name -- base.html's own <style>
        # block carries .scouting-card on every page regardless (the same trap
        # check_league_scoping.py documents; it caught this script once).
        check('example-question cards rendered', True, 'class="scouting-card"' in body)
        check('no offline notice', False, 'Scouting is offline' in body)
    else:
        SKIPPED.append('the reachable-model half (no local Ollama carrying '
                       f'{agent_reasoning.LLM_MODEL} at {real_url})')

    print('\nthe page, model unreachable')
    point_probe_at(DEAD_PORT_URL)
    response = client.get(f'/league/{slug}/scouting')
    body = response.get_data(as_text=True)
    check('renders 200, not a 500', 200, response.status_code)
    check('question form dropped', False, 'id="ask-form"' in body)
    check('example-question cards dropped', False, 'class="scouting-card"' in body)
    check('offline notice shown', True, 'Scouting is offline' in body)
    check('page hero still rendered', True, 'scouting-hero' in body)
    check('conversation section still rendered', True, 'No questions asked yet' in body)

    print('\nPOST while unreachable (the race a GET-time gate cannot cover)')
    posted = client.post(f'/league/{slug}/scouting', data={'question': 'who is kept?'})
    check('redirects, not a 500', 302, posted.status_code)
    followed = client.get(posted.headers['Location']).get_data(as_text=True)
    check('the redirect carries the offline message', True, 'airplane mode' in followed)

    print('\nask() itself')
    try:
        agent_reasoning.ask('yahoo', '9410', 'q', 'gate-check-thread', league_id=slug)
        check('raises LLMUnavailable', 'LLMUnavailable', 'no exception raised')
    except agent_reasoning.LLMUnavailable:
        check('raises LLMUnavailable', 'LLMUnavailable', 'LLMUnavailable')

    # A call that never reached the model must not spend one of the hour's
    # questions -- it doesn't, because the limit counts checkpointed messages
    # and only a successful turn appends one.
    thread_id = agent_reasoning.thread_id_for(user_id, 'yahoo', '9410')
    asked = len(agent_reasoning.questions_asked_in_last_hour(thread_id))
    check('a refused question is not counted against the hourly limit', 0, asked)

    agent_reasoning.OLLAMA_BASE_URL = real_url

    print()
    for note in SKIPPED:
        print(f'skipped: {note}')
    if FAILURES:
        print(f'\n{len(FAILURES)} check(s) failed: {FAILURES}')
        return 1
    print('\nScouting gates correctly on LLM availability.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
