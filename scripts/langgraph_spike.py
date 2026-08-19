"""LangGraph prototype spike, step 1 (2026-08-19).

Answers "why did this forecast change?" over real app/outcome_log.py +
outcome_log_history.json data. Single script, no persistence/checkpointing --
see the Obsidian plan (WS-6-agent-runtime/LangGraph_Prototype_Plan_2026-08-19.md)
for the full recommended build order this is step 1 of.

retrieve_node pulls a decision_id's current forecast (outcome_log.py) plus
every superseded forecast for it (outcome_log_history.json, added same day
as this script specifically so this spike would have real change signal to
reason over -- see app/outcome_log.py's module docstring). reason_node hands
that to Claude and asks for a plain-English explanation of the change.

Deliberately NOT wired into the Flask app yet -- that's step 2 in the plan,
gated on this spike proving useful first.

LLM: local via Ollama (free), not the Anthropic API -- switched 2026-08-19
when the API's per-call cost turned out to matter for a spike script run
repeatedly during development. Requires `brew install ollama`,
`brew services start ollama`, `ollama pull llama3.1:8b` (one-time, ~4.9GB),
and `pip install -r requirements-langgraph.txt`.

Usage:
    python3 scripts/langgraph_spike.py                    # picks a decision with history
    python3 scripts/langgraph_spike.py <decision_id>       # explain one specific decision
    python3 scripts/langgraph_spike.py --list               # list decision_ids with history
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: F401  (loads .env as a side effect)
from app.outcome_log import forecast_history, load_outcomes


class AgentState(TypedDict):
    decision_id: str
    retrieved_context: Dict[str, Any]
    answer: str


def _decisions_with_history() -> List[str]:
    """decision_ids that have at least one superseded forecast -- the only
    ones this spike's question ("why did this change?") is actually
    answerable for."""
    from app.outcome_log import load_outcome_history
    seen = {}
    for entry in load_outcome_history():
        seen.setdefault(entry['decision_id'], 0)
        seen[entry['decision_id']] += 1
    return sorted(seen, key=seen.get, reverse=True)


def retrieve_node(state: AgentState) -> AgentState:
    decision_id = state['decision_id']
    current = next((o for o in load_outcomes() if o['decision_id'] == decision_id), None)
    history = forecast_history(decision_id)
    state['retrieved_context'] = {'current': current, 'history': history}
    return state


def reason_node(state: AgentState) -> AgentState:
    from langchain_ollama import ChatOllama

    ctx = state['retrieved_context']
    current, history = ctx['current'], ctx['history']

    if current is None:
        state['answer'] = f"No entry found for decision_id {state['decision_id']!r}."
        return state
    if not history:
        state['answer'] = (
            f"{state['decision_id']} has never changed -- only one forecast on record "
            f"(logged {current['forecasted_at']}): {current['forecast']}."
        )
        return state

    timeline = history + [{
        'forecast': current['forecast'],
        'superseded_at': None,
        'forecast_method_version': current['forecast_method_version'],
    }]
    timeline_lines = '\n'.join(
        f"  {i+1}. {'(current)' if e['superseded_at'] is None else e['superseded_at']}: "
        f"{e['forecast']} [{e['forecast_method_version']}]"
        for i, e in enumerate(timeline)
    )

    prompt = (
        f"A fantasy football keeper-forecast system tracks predictions over time. "
        f"Here is the full forecast history for one decision ({state['decision_id']}), "
        f"oldest first:\n\n{timeline_lines}\n\n"
        f"In 2-3 plain-English sentences, explain what changed and speculate briefly "
        f"on why (e.g. a ranking shift, a different keeper being chosen ahead of this "
        f"player, a method_version change). Be concrete about the numbers you see -- "
        f"don't hedge with 'it's possible that'."
    )

    llm = ChatOllama(model='llama3.1:8b', temperature=0)
    response = llm.invoke(prompt)
    state['answer'] = response.content
    return state


def build_graph():
    from langgraph.graph import StateGraph, END

    graph = StateGraph(AgentState)
    graph.add_node('retrieve', retrieve_node)
    graph.add_node('reason', reason_node)
    graph.set_entry_point('retrieve')
    graph.add_edge('retrieve', 'reason')
    graph.add_edge('reason', END)
    return graph.compile()


def main():
    args = sys.argv[1:]

    if args and args[0] == '--list':
        ids = _decisions_with_history()
        if not ids:
            print('No decisions have a forecast history yet.')
            return
        print(f'{len(ids)} decision(s) with history:')
        for did in ids:
            print(f'  {did}')
        return

    if args:
        decision_id = args[0]
    else:
        ids = _decisions_with_history()
        if not ids:
            print('No decisions have a forecast history yet -- nothing to explain.')
            print('Run `python3 -m app keepers-board-export` after rankings move, or')
            print('toggle a keeper on /keepers-board, then retry.')
            return
        decision_id = ids[0]
        print(f'(no decision_id given, using the one with the most history: {decision_id})\n')

    app_graph = build_graph()
    result = app_graph.invoke({'decision_id': decision_id, 'retrieved_context': {}, 'answer': ''})
    print(f'--- {decision_id} ---')
    print(result['answer'])


if __name__ == '__main__':
    main()
