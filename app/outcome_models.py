"""DB-backed replacement for data/processed/outcome_log.json and
outcome_log_history.json.

Same landmine as the Sleeper/ESPN snapshots (snapshot_models.py) and the
LangGraph checkpoints: data/processed/ is gitignored and Railway's filesystem
is ephemeral, so every write appeared to succeed in-process and the very next
redeploy silently wiped it. This one bites harder than the snapshots did,
because the outcome log is the *only* record of what the app forecast and
when -- a snapshot re-syncs from the platform's API on the next sweep, a
forecast history cannot be re-derived from anything. Phase 6's Scouting agent
reasons over exactly this data (app/agent_reasoning.py), so on the live deploy
it has been reading a log that resets on every redeploy.

Two tables, mirroring the two JSON files rather than folding history into the
main table with a flag: they have genuinely different shapes and lifecycles.
An outcome row is *mutable* -- upserted by decision_id while pending, then
resolved in place -- while a history row is append-only and immutable, one per
superseded forecast, with several rows sharing a decision_id. A single table
would need the unique constraint to apply to some rows and not others.

Payloads (forecast, actual) stay JSON text rather than becoming columns: they
are per-decision-type shapes (a keeper forecast and a QB-adjustment forecast
share no fields), and outcome_log.py's readers already treat them as opaque
dicts. Everything the queries and accuracy_report() actually filter or group
on -- platform, league, decision_type, method_version, status -- is a real
column.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OutcomeEntry(Base):
    """One logged forecast, upserted by decision_id while still pending.

    decision_id is the primary key rather than a surrogate int + unique
    constraint: outcome_log.py's upsert has always been keyed on it, it is
    already a stable persisted string (see _id_slug's "Frozen" docstring),
    and there is no second natural row identity.
    """
    __tablename__ = 'outcome_entries'

    decision_id: Mapped[str] = mapped_column(String(320), primary_key=True)
    # Insertion order, so load_outcomes() reproduces the JSON array's order
    # exactly. It cannot be derived from forecasted_at: an upsert rewrites
    # that field in place, which would silently reshuffle the list every time
    # a pending forecast changed. Assigned by outcome_store on insert (max+1
    # rather than a DB sequence -- autoincrement only applies to the primary
    # key, which is decision_id here), never updated afterwards.
    seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    entity: Mapped[str] = mapped_column(String(160), nullable=False)
    # Nullable because qb_adjustment forecasts are league-wide, not per-team.
    team: Mapped[str] = mapped_column(String(160), nullable=True)
    forecast_json: Mapped[str] = mapped_column(Text, nullable=False)
    forecast_method_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # Stored as the same ISO string the JSON file held, not a DateTime: it is
    # read back into the entry dict verbatim and compared/serialized as text
    # by callers (agent_reasoning, the CLI reports). Converting here would
    # change what those see.
    forecasted_at: Mapped[str] = mapped_column(String(32), nullable=False)
    actual_json: Mapped[str] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[str] = mapped_column(String(32), nullable=True)
    delta_json: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='pending')
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class OutcomeHistoryEntry(Base):
    """One superseded forecast, append-only. Many rows per decision_id."""
    __tablename__ = 'outcome_history_entries'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    # The forecasted_at of the entry being replaced -- i.e. when the forecast
    # now being superseded was originally made.
    superseded_at: Mapped[str] = mapped_column(String(32), nullable=True)
    forecast_json: Mapped[str] = mapped_column(Text, nullable=False)
    forecast_method_version: Mapped[str] = mapped_column(String(64), nullable=True)
    appended_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False)
