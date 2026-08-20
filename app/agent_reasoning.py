"""LLM reasoning layer over the outcome log (WS-6 LangGraph prototype, step 2).

Pulled out of scripts/langgraph_spike.py (step 1, 2026-08-19) once it proved
useful enough to wire into the web app -- see the Obsidian plan
(WS-6-agent-runtime/LangGraph_Prototype_Plan_2026-08-19.md) for the full
recommended build order. web.py's /league/<slug>/ask route calls
ask(league, question, thread_id) as its single entry point, same
factored-out-of-web.py shape as keeper_service.py.

Retrieval (2026-08-19): no vectorstore. A league's outcome log is a few dozen
entries at most, so the whole thing (current forecasts + full change history,
scoped to this league via platform/platform_league_id) is dumped straight
into the prompt -- a real retriever would be solving a corpus-size problem
this app doesn't have yet. Revisit only if/when a league's log actually gets
big enough that this stops fitting a context window.

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
"""
import contextlib
import threading
from typing import Annotated, Any, Dict, List, TypedDict

from .db import DATABASE_URL, _IS_SQLITE
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
    user_query: str
    retrieved_context: str
    answer: str
    # {'question': str, 'answer': str} per past turn, oldest first. Grows via
    # _append_messages across invoke() calls sharing one thread_id -- see the
    # reducer's docstring for why this can't be a plain list field.
    messages: Annotated[List[Dict[str, str]], _append_messages]


def thread_id_for(user_id: int, platform: str, platform_league_id: str) -> str:
    return f'{user_id}_{platform}_{platform_league_id}'


class AskInProgress(Exception):
    """Raised by ask() when a question is already running on this thread_id.
    Not a rate limit (see MANUAL_SYNC_COOLDOWN_SECONDS in sync_scheduler.py
    for the time-based kind) -- this is an in-flight lock, since the actual
    risk is a slow synchronous Ollama call getting kicked off twice for the
    same conversation (double-click, a second tab) rather than someone
    asking too often. Clears the instant the first call finishes, success or
    error."""


# thread_id -> Lock. Module-level and in-memory, not a DB row: this app runs
# a single process (gunicorn workers=1, see docs/roadmap.md's Phase 1 note
# -- sync_scheduler.py's BackgroundScheduler already leans on the same
# assumption), so there's no cross-process case to cover, and an in-flight
# lock has no reason to outlive the process anyway.
_in_flight_lock = threading.Lock()
_in_flight_threads: Dict[str, bool] = {}


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
            lines.append(f"    earlier ({past['superseded_at']}): {past['forecast']}")
    return '\n'.join(lines) if lines else '(no forecasts logged for this league yet)'


def retrieve_node(state: AgentState) -> Dict[str, Any]:
    # Returns only this node's own delta, not the full state dict. Returning
    # state (including 'messages' echoed back unchanged) fed the same list
    # back through the reducer as if it were new, double-counting every turn
    # -- caught by testing a real 2-turn conversation and finding 3 history
    # entries instead of 2, not by re-reading the code.
    return {'retrieved_context': _league_log_text(state['platform'], state['platform_league_id'])}


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

    prompt = (
        f"You are answering a question about a fantasy football league's forecast "
        f"history (keeper picks, draft-slot predictions), tracked by an app that logs "
        f"every prediction and updates it over time as new data comes in.\n\n"
        f"Forecast log for this league:\n{state['retrieved_context']}\n\n"
        f"{prior_turns}"
        f"Question: {state['user_query']}\n\n"
        f"Answer in 2-4 plain-English sentences, grounded in the specific numbers "
        f"above. If the log doesn't contain enough to answer, say so plainly instead "
        f"of guessing. If the question refers back to something from earlier in the "
        f"conversation ('the first one', 'that player'), resolve it against the "
        f"conversation above, not the forecast log's own ordering."
    )
    llm = ChatOllama(model='llama3.1:8b', temperature=0)
    response = llm.invoke(prompt)
    answer = response.content
    return {'answer': answer, 'messages': [{'question': state['user_query'], 'answer': answer}]}


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


def ask(platform: str, platform_league_id: str, question: str, thread_id: str) -> str:
    """Single entry point web.py calls. One turn of a possibly-multi-turn
    conversation, threaded by thread_id (see thread_id_for) so a follow-up
    question in the same league re-enters the same checkpointer thread.

    Raises AskInProgress instead of running a second overlapping call for
    the same thread_id -- the Ollama call is slow and synchronous, so a
    double-click or a second tab would otherwise kick off two concurrent
    LLM calls competing for the same local machine's CPU/GPU. Cleared in a
    finally so a raised exception or a timeout can't leave a thread
    permanently locked out.
    """
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
                 'user_query': question, 'retrieved_context': '', 'answer': '', 'messages': []},
                config=config,
            )
        return result['answer']
    finally:
        with _in_flight_lock:
            _in_flight_threads.pop(thread_id, None)


def conversation_history(thread_id: str) -> List[Dict[str, str]]:
    """Past question/answer turns for this thread, oldest first -- for
    rendering the /ask page's transcript. Empty list for a fresh thread
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
