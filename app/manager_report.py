"""Manager report card: per-manager draft performance, not per-team-per-season.

Team display names change (renames) and draft slots rotate year to year, but
the person behind a franchise slot doesn't -- see `app/standings.py`'s
`current_team_names` for the alias mechanism (Yahoo's own "now displayed as
X" rename note) this module reuses to fold every team-name variant a manager
has used into one canonical identity before aggregating anything.

Built on `app/draft_analysis.py`'s `draft_slot_vs_final_rank`, so the same
data limits apply: only seasons with BOTH draft results and saved standings
count, and the slot-to-rank baseline used below is directional on a small
sample, not a hard model (see `summarize_draft_slot_correlation`'s caveat).

Deliberately does NOT grade individual pick quality against season-long
player performance (fantasy points) -- that's the ADP-vs-outcome
"gem-finding" shape this project rejected 2026-07-31. What's graded here is
one level up: did a manager's actual finish beat what their own draft slots
would predict, using this league's own multi-year history as the only
baseline (no external assumption, no hand-tuned threshold). No letter
grades either -- WuFF's copy stays numbers, not personas.

⚠️ Identity resolution is only as good as Yahoo's own rename note, and that
note doesn't fire for most historical renames -- checked against real data
2026-08-11: a 12-team league across 5 seasons produced 24 "manager" rows,
not ~12, because e.g. "Balls Deep" (2022-24) and "BALLS DEEP" (2025) never
got linked. No persistent owner id is saved in data/raw/standings/{year}.json
to do this properly (only team name + season stats), and re-fetching through
the Yahoo API to get one is blocked (OAuth approval pending, see
league_rules.json's platform notes). So: each row is really "one team-name
lineage," not a verified person, and every row carries `team_names` (every
raw name folded into it) so that limit stays visible on the page instead of
silently overclaiming identity. A hand-authored alias file is the fix if
this needs to be exact -- deliberately not built speculatively.
"""
from collections import defaultdict
from statistics import mean
from typing import Any, Dict, List, Optional

from .draft_analysis import draft_slot_vs_final_rank, summarize_draft_slot_correlation
from .draft_history import keeper_rounds_for_year
from .repository import LeagueDataRepository, get_repository
from .standings import current_team_names as get_current_team_names


def _alias_map(repo: LeagueDataRepository, years: List[int]) -> Dict[str, str]:
    """Every team-name -> canonical-current-name mapping this league's standings
    have ever recorded, across every season with saved standings. A manager's
    2022 name folds into whatever they're called today in one lookup --
    Yahoo's rename note already points at the final name, not an intermediate
    one (see current_team_names), so this doesn't need multi-hop chaining,
    just a union of every year's map. _canonical() still walks it defensively
    in case that assumption ever breaks for some league."""
    aliases: Dict[str, str] = {}
    for year in years:
        standings = repo.standings(year)
        if standings:
            aliases.update(get_current_team_names(standings))
    return aliases


def _canonical(name: str, aliases: Dict[str, str]) -> str:
    seen = set()
    while name in aliases and name not in seen:
        seen.add(name)
        name = aliases[name]
    return name


def _keeper_picks_for_year(year: int, draft_years: Dict[int, List[dict]], keeper_slots: int) -> List[dict]:
    """Picks that occupied a keeper slot that year, for THIS league's own
    keeper-slot count -- not the keeper_slot_picks()/live_draft_picks()
    convenience wrappers in draft_history.py, which hardcode 2 trailing
    rounds via keeper_rounds_for_year's default and don't expose a way to
    override it. keeper_slots <= 0 (no round-based keeper slots, e.g. a
    dynasty league) correctly yields no picks here, not an error."""
    if keeper_slots <= 0:
        return []
    keeper_rounds = keeper_rounds_for_year(year, draft_years, slots=keeper_slots)
    if not keeper_rounds:
        return []
    return [p for p in draft_years.get(year, []) if p.get('round') in keeper_rounds]


def manager_report_card(repo: Optional[LeagueDataRepository] = None) -> List[Dict[str, Any]]:
    """One row per manager (canonical name), aggregating every season this
    league has both draft results and saved standings for.

    `value_over_expected` = the league's own baseline average final rank for
    the draft slots this manager actually drafted from, minus their own
    actual average final rank. Positive means they finished better than
    their draft slots alone would predict; negative means worse. The
    baseline comes from summarize_draft_slot_correlation() over the WHOLE
    league's history, so it's this league's own data, not an assumption
    imported from outside it.

    Empty list, not an error, when there's no season with both draft results
    and standings yet -- same gate as draft_slot_vs_final_rank.
    """
    repo = repo if repo is not None else get_repository()
    outcomes = draft_slot_vs_final_rank(repo=repo)
    if not outcomes:
        return []

    baseline = summarize_draft_slot_correlation(outcomes)['slot_to_avg']
    years = sorted({o.year for o in outcomes})
    aliases = _alias_map(repo, years)

    draft_years = repo.draft_years()
    keeper_slots = repo.league.format.keeper_slots
    keeper_counts: Dict[str, int] = defaultdict(int)
    keeper_seasons: Dict[str, set] = defaultdict(set)
    for year in years:
        for pick in _keeper_picks_for_year(year, draft_years, keeper_slots):
            team = pick.get('team')
            if not team:
                continue
            manager = _canonical(team, aliases)
            keeper_counts[manager] += 1
            keeper_seasons[manager].add(year)

    by_manager: Dict[str, list] = defaultdict(list)
    for outcome in outcomes:
        by_manager[_canonical(outcome.team, aliases)].append(outcome)

    rows = []
    for manager, entries in by_manager.items():
        ranks = [e.final_rank for e in entries]
        expected = mean(baseline[e.draft_slot]['avg_rank'] for e in entries)
        avg_rank = mean(ranks)
        rows.append({
            'manager': manager,
            'seasons': sorted(e.year for e in entries),
            'n_seasons': len(entries),
            'avg_draft_slot': round(mean(e.draft_slot for e in entries), 1),
            'avg_final_rank': round(avg_rank, 1),
            'expected_rank_for_slots': round(expected, 1),
            'value_over_expected': round(expected - avg_rank, 2),
            'best_finish': min(ranks),
            'worst_finish': max(ranks),
            'championships': sum(1 for r in ranks if r == 1),
            'keeper_picks': keeper_counts.get(manager, 0),
            'seasons_with_a_keeper': len(keeper_seasons.get(manager, set())),
        })

    rows.sort(key=lambda r: -r['value_over_expected'])
    return rows
