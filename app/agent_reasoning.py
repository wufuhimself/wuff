"""LLM reasoning layer over the outcome log (WS-6 LangGraph prototype, step 2).

Pulled out of scripts/langgraph_spike.py (step 1, 2026-08-19) once it proved
useful enough to wire into the web app -- see the Obsidian plan
(WS-6-agent-runtime/LangGraph_Prototype_Plan_2026-08-19.md) for the full
recommended build order. web.py's /league/<slug>/scouting route (branded
"Scouting" in the UI, renamed 2026-08-19 -- was "Ask") calls
ask(league, question, thread_id) as its single entry point, same
factored-out-of-web.py shape as keeper_service.py.

Retrieval (2026-08-19): no vectorstore. A league's outcome log is a few dozen
entries at most, so the whole thing (current forecasts + full change history,
scoped to this league via platform/platform_league_id) is dumped straight
into the prompt -- a real retriever would be solving a corpus-size problem
this app doesn't have yet. Revisit only if/when a league's log actually gets
big enough that this stops fitting a context window.

Draft patterns folded in (2026-08-20): the standalone /league/<slug>/draft-patterns
page (position mix by round, from app/draft_patterns.py's position_mix_by_round())
is retired -- its data is now part of retrieved_context here instead, so
Scouting can answer "what does this league draft in round 3" the same way it
answers outcome-log questions, and there's one fewer static page to maintain.
Needs a repo, which platform/platform_league_id alone can't produce (repository_for()
takes a League, not a platform pair) -- ask() takes the wuff league_id slug
(league.league_id, already in scope at web.py's call site) for exactly this,
resolved via get_repository() inside retrieve_node.

Checkpointing (2026-08-19, revised same day): PostgresSaver against
DATABASE_URL when it's set to Postgres (production -- same DB
app/db.py's SQLAlchemy models already use there), SqliteSaver at
data/processed/agent_checkpoints.db otherwise (local dev, no Postgres
running). First cut used only SqliteSaver -- looked fine locally but
data/processed/ is gitignored and Railway's container filesystem is
ephemeral, so every redeploy silently wiped every user's conversation
history, the exact bug already fixed once for Sleeper/ESPN sync
snapshots (see app/snapshot_store.py) and still open for
outcome_log.json. Caught by the user asking "is this actually
persisted?" after using the feature live, not by testing -- the local
sqlite file looked completely correct, which is exactly what made this
easy to miss: the bug only exists in the gap between "works on my
machine" and "survives a deploy." thread_id is
f"{user_id}_{platform}_{platform_league_id}" (user + league, per the
plan) so each user's conversation with each league's agent is its own
multi-turn thread and users can't see each other's threads.

LLM: local via Ollama (see scripts/langgraph_spike.py's docstring for why --
API cost, not a technical constraint). Swap point is the ChatOllama(...) call
below if that ever changes.

Availability gate (2026-08-21): that local-Ollama decision made Scouting a
dev-only feature in practice -- production (Railway) runs no Ollama and sets
no OLLAMA_BASE_URL, so every question asked on the live app died inside
ChatOllama.invoke() with a connection error while the page advertised itself
in the nav as a working feature. llm_available() probes for a reachable
server that has LLM_MODEL pulled; web.py renders an offline state instead of
the question form when it's False, and ask() raises LLMUnavailable for the
race where it disappears in between. This is a gate, not a fix -- the actual
production-inference question (self-host vs. hosted model) is still open, see
docs/roadmap.md Phase 6.
"""
import contextlib
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, TypedDict

from .db import DATABASE_URL, _IS_SQLITE
from .draft_patterns import position_mix_by_round
from .outcome_log import load_outcome_history, load_outcomes
from .paths import PROCESSED_DIR, ensure_parent_dir

AGENT_CHECKPOINT_FILE = PROCESSED_DIR / 'agent_checkpoints.db'

# PostgresSaver.setup() creates its tables if missing and MUST be called
# before first use, but is safe/cheap to call more than once -- guarded here
# purely to avoid a redundant round-trip on every single ask()/
# conversation_history() call in the common case.
_postgres_ready = False  # pylint: disable=invalid-name

DEFAULT_PLATFORM = 'yahoo'
DEFAULT_PLATFORM_LEAGUE_ID = '9410'

LLM_MODEL = 'llama3.1:8b'
# Ollama runs on the same machine in dev, so localhost is the right default;
# OLLAMA_BASE_URL exists so a deployment can point at a host that actually
# has one. Production (Railway) sets nothing and runs no Ollama, which is
# precisely why llm_available() below exists -- before it, every Scouting
# question on the live app raised a connection error inside reason_node's
# ChatOllama call and 500'd the page, with the feature still linked in the
# nav as if it worked.
OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434').rstrip('/')

# llm_available() is called on every Scouting page render, so its probe is
# cached briefly. One TTL for both answers, deliberately short: too long and
# starting Ollama locally leaves the page insisting it's offline, too short
# and an unreachable remote host pays its timeout on every render.
_AVAILABILITY_TTL_SECONDS = 30
_availability_cache: Dict[str, Any] = {'checked_at': None, 'available': False}


def _append_messages(existing: list, new: list) -> list:
    """Reducer for the messages field: LangGraph merges state by calling this
    with (accumulated-so-far, this-invoke's-return-value) instead of
    replacing -- without it, invoke()'s fresh AgentState dict each call would
    silently wipe prior turns rather than growing the conversation. This is
    what actually makes checkpointing multi-turn; a plain (non-Annotated)
    list field looked like it should work in the first draft of this module
    and didn't -- reason_node saw only the current turn's own question, with
    zero memory of what was asked or answered before it (caught by testing a
    real 2-turn conversation, not by re-reading the code)."""
    return existing + new


class AgentState(TypedDict):
    platform: str
    platform_league_id: str
    league_id: str  # wuff registry slug, e.g. 'frank-gore' -- see get_repository()
    user_query: str
    retrieved_context: str
    answer: str
    # {'question': str, 'answer': str} per past turn, oldest first. Grows via
    # _append_messages across invoke() calls sharing one thread_id -- see the
    # reducer's docstring for why this can't be a plain list field.
    messages: Annotated[List[Dict[str, str]], _append_messages]


def thread_id_for(user_id: int, platform: str, platform_league_id: str) -> str:
    return f'{user_id}_{platform}_{platform_league_id}'


class LLMUnavailable(Exception):
    """Raised by ask() when no Ollama server carrying LLM_MODEL can be
    reached. A missing model is an environment fact, not a user error --
    web.py gates the whole Scouting form on llm_available() so the page says
    so up front, and this exception covers the narrower race where the model
    goes away between rendering the form and submitting it."""


def llm_available(force: bool = False) -> bool:
    """Whether an Ollama server carrying LLM_MODEL is actually reachable.

    Checks the model is *present*, not merely that something answers on the
    port: a running Ollama that never pulled llama3.1:8b fails at invoke()
    exactly like an absent one does, so "the host responded" alone would
    still hand the user a 500 after they'd typed a question.

    Cached for _AVAILABILITY_TTL_SECONDS (see the constant) -- pass
    force=True to skip the cache.
    """
    import requests  # pylint: disable=import-outside-toplevel

    now = datetime.now(timezone.utc)
    checked_at = _availability_cache['checked_at']
    if not force and checked_at is not None:
        if (now - checked_at).total_seconds() < _AVAILABILITY_TTL_SECONDS:
            return bool(_availability_cache['available'])

    available = False
    try:
        response = requests.get(f'{OLLAMA_BASE_URL}/api/tags', timeout=2)
        response.raise_for_status()
        names = [m.get('name', '') for m in response.json().get('models', [])]
        available = LLM_MODEL in names
    except (requests.RequestException, ValueError):
        # Unreachable host, non-200, or a body that isn't the JSON we expect
        # all mean the same thing to a caller: don't send a question at this.
        available = False

    _availability_cache['checked_at'] = now
    _availability_cache['available'] = available
    return available


class AskInProgress(Exception):
    """Raised by ask() when a question is already running on this thread_id.
    Not a rate limit (see QuestionLimitReached below, or
    MANUAL_SYNC_COOLDOWN_SECONDS in sync_scheduler.py for the time-based
    kind) -- this is an in-flight lock, since the actual risk is a slow
    synchronous Ollama call getting kicked off twice for the same
    conversation (double-click, a second tab) rather than someone asking too
    often. Clears the instant the first call finishes, success or error."""


class QuestionLimitReached(Exception):
    """Raised by ask() when this thread_id has already asked
    QUESTIONS_PER_HOUR_LIMIT questions in the last hour. Carries
    `retry_after` (timedelta) so callers can show a countdown."""

    def __init__(self, retry_after: timedelta):
        self.retry_after = retry_after
        super().__init__(f'Question limit reached; try again in {retry_after}.')


QUESTIONS_PER_HOUR_LIMIT = 3


def questions_asked_in_last_hour(thread_id: str) -> List[str]:
    """asked_at timestamps (ISO strings) for this thread's questions in the
    last hour, oldest first. Reads the checkpointed `messages` list rather
    than a separate table/counter -- it's already the durable record of
    every turn (same Postgres-in-prod / sqlite-locally persistence as the
    rest of this module), so the limit needs no persistence of its own.
    Entries logged before 'asked_at' existed have no such key and are
    silently excluded (undercounts rather than misdating them into the
    window -- the safe direction for a rate limit to be wrong in)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    recent = []
    for turn in conversation_history(thread_id):
        asked_at = turn.get('asked_at')
        if not asked_at:
            continue
        when = datetime.fromisoformat(asked_at)
        if when >= cutoff:
            recent.append(asked_at)
    return recent


# thread_id -> Lock. Module-level and in-memory, not a DB row: this app runs
# a single process (gunicorn workers=1, see docs/roadmap.md's Phase 1 note
# -- sync_scheduler.py's BackgroundScheduler already leans on the same
# assumption), so there's no cross-process case to cover, and an in-flight
# lock has no reason to outlive the process anyway.
_in_flight_lock = threading.Lock()
_in_flight_threads: Dict[str, bool] = {}


def _mmddyyyy(iso_timestamp: str) -> str:
    """ISO timestamp -> mm-dd-yyyy for anything shown to the model/user --
    the log stores full ISO (forecasted_at/superseded_at) for exact sort
    order, but that precision has no value in a chat answer and 'on
    2026-08-19T15:22:31' reads worse than 'on 08-19-2026'."""
    return datetime.fromisoformat(iso_timestamp).strftime('%m-%d-%Y')


def _league_log_text(platform: str, platform_league_id: str) -> str:
    """Every current + superseded forecast for one league, rendered as plain
    text for the prompt. Mirrors outcome_log.py's own platform/platform_league_id
    scoping (defaults fall back to the Yahoo frank-gore league, same as
    load_outcomes()'s callers elsewhere)."""
    current = [
        o for o in load_outcomes()
        if (o.get('platform') or DEFAULT_PLATFORM) == platform
        and (o.get('platform_league_id') or DEFAULT_PLATFORM_LEAGUE_ID) == platform_league_id
    ]
    history_by_id: Dict[str, List[Dict[str, Any]]] = {}
    for entry in load_outcome_history():
        history_by_id.setdefault(entry['decision_id'], []).append(entry)

    lines = []
    for entry in current:
        lines.append(
            f"- {entry['entity']} (team: {entry.get('team') or 'n/a'}, "
            f"season {entry['season']}, {entry['decision_type']}): "
            f"current forecast {entry['forecast']} "
            f"[{entry['forecast_method_version']}], status={entry['status']}"
        )
        for past in history_by_id.get(entry['decision_id'], []):
            lines.append(f"    earlier ({_mmddyyyy(past['superseded_at'])}): {past['forecast']}")
    return '\n'.join(lines) if lines else '(no forecasts logged for this league yet)'


def _draft_patterns_text(league_id: str) -> str:
    """This league's own position mix by round, rendered as plain text for
    the prompt -- replaces the retired /league/<slug>/draft-patterns page
    (see this module's docstring). Same data as that page's one remaining
    table (position_mix_by_round(); the "when each position comes off the
    board" / "Nth player" sections were dropped from the page 2026-08-20
    and never made it here either -- position_timing() stays a mock-draft
    input only, not a Scouting fact). Empty league_id (no repo context)
    or a league with no draft history both return the same "no data"
    line rather than raising -- keeps retrieve_node from needing a
    try/except for a case that's a normal empty state, not an error."""
    if not league_id:
        return '(no draft history available for this league)'
    from .repository import get_repository  # pylint: disable=import-outside-toplevel
    try:
        mix = position_mix_by_round(get_repository(league_id))
    except Exception:  # pylint: disable=broad-exception-caught
        return '(no draft history available for this league)'
    if not mix:
        return '(no draft history available for this league)'

    lines = []
    for round_num, data in sorted(mix.items()):
        shares = ', '.join(
            f'{position} {round(fraction * 100)}%'
            for position, fraction in data['mix'].items()
        )
        lines.append(f"- Round {round_num} ({data['n']} picks): {shares}")
    return '\n'.join(lines)


def _league_has_keepers(league_id: str) -> bool:
    """Whether this league keeps players at all.

    Gates the prompt's description of what the forecast log contains: telling
    the model a redraft league's log holds "keeper picks" invites it to answer
    about keepers that cannot exist, which is exactly the ungrounded guessing
    the page promises it does not do. Resolved through league_service so a
    league's own saved rules win over the registry default, matching what the
    keeper board and the nav both key on. Unknown/unresolvable leagues report
    False -- the narrower prompt is the safe direction.
    """
    if not league_id:
        return False
    from .league_service import resolve_league  # pylint: disable=import-outside-toplevel
    try:
        league = resolve_league(league_id)
    except Exception:  # pylint: disable=broad-exception-caught
        return False
    return bool(league and league.format.keeper_slots)


def has_resolved_forecasts(platform: str, platform_league_id: str) -> bool:
    """True once this league has at least one resolved forecast (a real
    draft has happened and been matched against what was predicted) --
    lets the Scouting page switch from anticipate-the-draft example
    questions to forecast-vs-actual ones. Same platform/platform_league_id
    scoping as _league_log_text, so it agrees with what the LLM is
    actually shown."""
    return any(
        o['status'] == 'resolved'
        for o in load_outcomes()
        if (o.get('platform') or DEFAULT_PLATFORM) == platform
        and (o.get('platform_league_id') or DEFAULT_PLATFORM_LEAGUE_ID) == platform_league_id
    )


def retrieve_node(state: AgentState) -> Dict[str, Any]:
    # Returns only this node's own delta, not the full state dict. Returning
    # state (including 'messages' echoed back unchanged) fed the same list
    # back through the reducer as if it were new, double-counting every turn
    # -- caught by testing a real 2-turn conversation and finding 3 history
    # entries instead of 2, not by re-reading the code.
    forecast_log = _league_log_text(state['platform'], state['platform_league_id'])
    draft_patterns = _draft_patterns_text(state.get('league_id', ''))
    context = (
        f"Forecast log:\n{forecast_log}\n\n"
        f"Draft history -- position mix by round:\n{draft_patterns}"
    )
    return {'retrieved_context': context}


def reason_node(state: AgentState) -> Dict[str, Any]:
    # Deferred imports throughout this module (not just style, see other
    # app/*.py import-outside-toplevel disables) -- these packages are in
    # requirements.txt as of 2026-08-19 (this module is a live feature now,
    # not a spike), so import-outside-toplevel is the only real disable
    # needed; import-error is kept alongside it defensively in case
    # pylint's venv ever drifts, harmless either way.
    from langchain_ollama import ChatOllama  # pylint: disable=import-outside-toplevel,import-error

    prior_turns = ''
    if state['messages']:
        prior_lines = [f"Q: {m['question']}\nA: {m['answer']}" for m in state['messages']]
        prior_turns = 'Earlier in this conversation:\n' + '\n\n'.join(prior_lines) + '\n\n'

    # Redraft leagues have no keeper picks in their log at all -- naming them
    # here would invite an answer about data that cannot exist.
    forecast_kinds = ('keeper picks, draft-slot predictions'
                      if _league_has_keepers(state.get('league_id', ''))
                      else 'draft-slot predictions')
    prompt = (
        f"You are answering a question about a fantasy football league: its forecast "
        f"history ({forecast_kinds}, tracked by an app that logs "
        f"every prediction and updates it over time) and its own real draft history "
        f"(what position gets picked in which round).\n\n"
        f"{state['retrieved_context']}\n\n"
        f"{prior_turns}"
        f"Question: {state['user_query']}\n\n"
        f"Answer in 2-4 plain-English sentences, grounded in the specific numbers "
        f"above. If neither section contains enough to answer, say so plainly instead "
        f"of guessing. If the question refers back to something from earlier in the "
        f"conversation ('the first one', 'that player'), resolve it against the "
        f"conversation above, not the data above's own ordering. Dates are already "
        f"formatted mm-dd-yyyy -- keep that exact format in your answer, never expand "
        f"it back into an ISO timestamp."
    )
    llm = ChatOllama(model=LLM_MODEL, base_url=OLLAMA_BASE_URL, temperature=0)
    response = llm.invoke(prompt)
    answer = response.content
    # asked_at powers the hourly question cap (QUESTIONS_PER_HOUR_LIMIT
    # below) -- reads this same checkpointed messages list rather than a
    # separate table, so the limit needs no persistence of its own.
    asked_at = datetime.now(timezone.utc).isoformat()
    return {'answer': answer,
            'messages': [{'question': state['user_query'], 'answer': answer, 'asked_at': asked_at}]}


@contextlib.contextmanager
def _checkpointer():
    """The right checkpointer for this environment, as a context manager --
    PostgresSaver (DATABASE_URL) in production, SqliteSaver (a local file)
    otherwise. Mirrors app/db.py's own _IS_SQLITE branch rather than
    inventing a second way to detect the environment.

    Each call opens its own connection (cheap, matches this app's other
    sqlite-via-context-manager call sites) rather than holding one open for
    the process lifetime -- avoids a shared-connection-across-requests
    footgun in a threaded Flask dev server.

    The pylint disable on each nested `with` below: pylint flags them as a
    possible missing-cleanup risk, but this is the standard safe pattern --
    contextlib.contextmanager's machinery correctly throws exceptions back
    into the generator body at the yield point, so the nested `with`'s
    __exit__ still runs. Verified directly: an exception raised inside a
    real `with _checkpointer():` block propagates correctly AND the
    connection can be cleanly reopened right after, with no leaked
    lock/connection.
    """
    global _postgres_ready  # pylint: disable=global-statement
    if _IS_SQLITE:
        from langgraph.checkpoint.sqlite import SqliteSaver  # pylint: disable=import-outside-toplevel,import-error
        ensure_parent_dir(AGENT_CHECKPOINT_FILE)
        with SqliteSaver.from_conn_string(str(AGENT_CHECKPOINT_FILE)) as checkpointer:
            yield checkpointer
    else:
        from langgraph.checkpoint.postgres import PostgresSaver  # pylint: disable=import-outside-toplevel,import-error
        with PostgresSaver.from_conn_string(DATABASE_URL) as checkpointer:  # pylint: disable=contextmanager-generator-missing-cleanup
            if not _postgres_ready:
                checkpointer.setup()
                _postgres_ready = True
            yield checkpointer


def _build_graph(checkpointer):
    from langgraph.graph import StateGraph, END  # pylint: disable=import-outside-toplevel,import-error

    graph = StateGraph(AgentState)
    graph.add_node('retrieve', retrieve_node)
    graph.add_node('reason', reason_node)
    graph.set_entry_point('retrieve')
    graph.add_edge('retrieve', 'reason')
    graph.add_edge('reason', END)
    return graph.compile(checkpointer=checkpointer)


def ask(platform: str, platform_league_id: str, question: str, thread_id: str,
        league_id: str = '') -> str:
    """Single entry point web.py calls. One turn of a possibly-multi-turn
    conversation, threaded by thread_id (see thread_id_for) so a follow-up
    question in the same league re-enters the same checkpointer thread.

    league_id is the wuff registry slug (league.league_id, e.g.
    'frank-gore') -- separate from platform/platform_league_id because
    repository_for() needs a League object, not a platform pair. Used to
    pull this league's own draft history into retrieved_context (see
    _draft_patterns_text). Defaults to '' for a caller with no league
    context (e.g. a unit test), which just means the draft-history section
    reads as empty rather than raising.

    Raises AskInProgress instead of running a second overlapping call for
    the same thread_id -- the Ollama call is slow and synchronous, so a
    double-click or a second tab would otherwise kick off two concurrent
    LLM calls competing for the same local machine's CPU/GPU. Cleared in a
    finally so a raised exception or a timeout can't leave a thread
    permanently locked out.

    Raises QuestionLimitReached if this thread has already asked
    QUESTIONS_PER_HOUR_LIMIT questions in the last hour -- checked before
    the in-flight lock so a rate-limited call never even contends for it.

    Raises LLMUnavailable if no Ollama server carrying LLM_MODEL is
    reachable. Checked first, ahead of the rate limit: an unreachable model
    is the more fundamental state, and a failed call must not consume one of
    the hour's questions (it doesn't -- the limit counts checkpointed
    messages, which only get appended on a successful turn).
    """
    if not llm_available():
        raise LLMUnavailable('No Ollama server carrying '
                             f'{LLM_MODEL} at {OLLAMA_BASE_URL}.')

    recent = questions_asked_in_last_hour(thread_id)
    if len(recent) >= QUESTIONS_PER_HOUR_LIMIT:
        oldest = datetime.fromisoformat(min(recent))
        retry_after = oldest + timedelta(hours=1) - datetime.now(timezone.utc)
        raise QuestionLimitReached(retry_after)

    with _in_flight_lock:
        if _in_flight_threads.get(thread_id):
            raise AskInProgress(f'A question is already in progress for {thread_id}.')
        _in_flight_threads[thread_id] = True

    try:
        with _checkpointer() as checkpointer:
            graph = _build_graph(checkpointer)
            config = {'configurable': {'thread_id': thread_id}}
            # messages: [] here is the per-invoke contribution the reducer
            # appends with -- the checkpointer supplies whatever accumulated
            # from earlier turns on this thread_id, this call adds one more
            # via reason_node's own return (see _append_messages).
            result = graph.invoke(
                {'platform': platform, 'platform_league_id': platform_league_id,
                 'league_id': league_id,
                 'user_query': question, 'retrieved_context': '', 'answer': '', 'messages': []},
                config=config,
            )
        return result['answer']
    finally:
        with _in_flight_lock:
            _in_flight_threads.pop(thread_id, None)


def conversation_history(thread_id: str) -> List[Dict[str, str]]:
    """Past question/answer turns for this thread, oldest first -- for
    rendering the Scouting page's transcript. Empty list for a fresh thread
    (nothing asked yet, or -- sqlite only -- the checkpoint file doesn't
    exist yet).

    Reads the `messages` field off the latest checkpoint rather than
    reconstructing history from raw per-super-step snapshots: LangGraph
    checkpoints every internal step (retrieve, reason, ...), not one per
    logical turn, so scraping snapshot-by-snapshot produced duplicate and
    misordered turns in testing -- `messages`, accumulated via a reducer, is
    the one field that's already exactly "one entry per turn" by
    construction."""
    if _IS_SQLITE and not AGENT_CHECKPOINT_FILE.exists():
        return []

    with _checkpointer() as checkpointer:
        config = {'configurable': {'thread_id': thread_id}}
        latest = checkpointer.get(config)
        if not latest:
            return []
        return latest.get('channel_values', {}).get('messages', [])
