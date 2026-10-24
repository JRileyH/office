"""SQLite: the project registry, the job queue and each job's steps, Riggy's notes to jobs,
and run records.

One process owns the database and every call is short, so a single connection is enough.
"""

from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'standard',
    model TEXT NOT NULL DEFAULT 'opus',
    effort TEXT NOT NULL DEFAULT 'high',
    room_id TEXT UNIQUE,
    paused INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL REFERENCES projects(slug),
    kind TEXT NOT NULL,              -- new | change
    request TEXT NOT NULL,           -- Riggy's words that started the job
    playbook TEXT NOT NULL,
    phase INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 0,
    resume_session TEXT,             -- set when a blocked step should continue its session
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_at TEXT,
    issue INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    text TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    phase TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT,
    session_id TEXT,
    num_turns INTEGER,
    cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    log_path TEXT
);
-- A job's steps, in order, written from its playbook when it starts. office works
-- through them one at a time; a step that fails gets a fix and a retry inserted after it.
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- agent, or one of office's own (playbook.CODE_STEPS)
    name TEXT NOT NULL,
    max_turns INTEGER,
    optional INTEGER NOT NULL DEFAULT 0,  -- Riggy may skip it
    state TEXT NOT NULL DEFAULT 'pending',
    summary TEXT,                    -- what it did, or what it's waiting for
    problem TEXT,                    -- a fix step's: what to fix
    error TEXT,                      -- why the last attempt failed
    links TEXT NOT NULL DEFAULT '{}',  -- JSON {label: url}: the PR, commit, CI run, site
    started_at TEXT,
    ended_at TEXT,
    model TEXT,                      -- Riggy's choice for this step, over the defaults
    effort TEXT
);
-- What the projects page shows for a live project (cards.py).
CREATE TABLE IF NOT EXISTS cards (
    slug TEXT PRIMARY KEY,
    tagline TEXT NOT NULL,
    description TEXT NOT NULL,
    kind TEXT NOT NULL,              -- game | app | tool | site: says what its button does
    tags TEXT NOT NULL DEFAULT '[]', -- JSON list, a few words each
    image TEXT,                      -- screenshot file name in <state>/cards, once one's taken
    updated_at TEXT NOT NULL
);
-- The model and effort for each kind of agent step (its name: spec, docs, fix…), set on
-- the status page. Empty means not set here. The name '*' is every other kind.
CREATE TABLE IF NOT EXISTS defaults (
    name TEXT PRIMARY KEY,
    model TEXT NOT NULL DEFAULT '',
    effort TEXT NOT NULL DEFAULT ''
);
"""

# Columns added after the first release, added to older databases on open.
MIGRATIONS = {
    "projects": {
        "model": "TEXT NOT NULL DEFAULT 'opus'",
        "effort": "TEXT NOT NULL DEFAULT 'high'",
    },
    "runs": {"step_id": "INTEGER"},
    "steps": {"optional": "INTEGER NOT NULL DEFAULT 0", "model": "TEXT", "effort": "TEXT"},
    "notes": {"step_id": "INTEGER"},  # a note for one step rather than the next
}

# Jobs in these states hold or want the worker slot, or wait on Riggy; anything else is finished.
ACTIVE = ("queued", "running", "waiting", "parked")
# Steps in these states are still to do: the first of them is the job's current step.
OPEN = ("pending", "running", "waiting")
MODES = ("reckless", "standard", "rigorous")
MODELS = ("fable", "opus", "sonnet", "haiku")
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def later(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat(timespec="seconds")


@dataclass
class Project:
    slug: str
    name: str
    summary: str
    mode: str
    model: str  # "" for each step's default (plan.engine)
    effort: str
    room_id: str | None
    paused: bool
    created_at: str


@dataclass
class Card:
    slug: str
    tagline: str
    description: str
    kind: str
    tags: list[str] = field(default_factory=list)
    image: str | None = None
    updated_at: str = ""


@dataclass
class Job:
    id: int
    slug: str
    kind: str
    request: str
    playbook: str
    phase: int
    state: str
    priority: int
    resume_session: str | None
    attempts: int
    retry_at: str | None
    issue: int | None
    created_at: str
    updated_at: str


@dataclass
class Step:
    id: int  # 0 for steps a job will get but hasn't yet (see plan.planned)
    job_id: int
    position: int
    kind: str
    name: str
    max_turns: int | None = None
    optional: bool = False
    state: str = "pending"  # pending | running | waiting | done | skipped | failed
    summary: str | None = None
    problem: str | None = None
    error: str | None = None
    links: dict[str, str] = field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None
    model: str | None = None  # Riggy's choice for this step; None for the defaults
    effort: str | None = None


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for column, decl in columns.items():
                if column not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        if self.get("project_engines") is None:
            # Projects used to always name a model and effort, opus and high unless Riggy said
            # otherwise. Those now come from each step's defaults, which start out the same.
            self.db.execute("UPDATE projects SET model = '' WHERE model = 'opus'")
            self.db.execute("UPDATE projects SET effort = '' WHERE effort = 'high'")
            self.put("project_engines", "1")

    # --- kv -------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def put(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # --- projects -------------------------------------------------------------

    def add_project(
        self, slug: str, name: str, summary: str, mode: str = "standard", model: str = "", effort: str = ""
    ) -> Project:
        self.db.execute(
            "INSERT INTO projects (slug, name, summary, mode, model, effort, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, name, summary, mode, model, effort, now()),
        )
        return self.project(slug)

    def project(self, slug: str) -> Project | None:
        row = self.db.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
        return _project(row) if row else None

    def project_by_room(self, room_id: str) -> Project | None:
        row = self.db.execute("SELECT * FROM projects WHERE room_id = ?", (room_id,)).fetchone()
        return _project(row) if row else None

    def projects(self) -> list[Project]:
        return [_project(r) for r in self.db.execute("SELECT * FROM projects ORDER BY created_at DESC")]

    def set_room(self, slug: str, room_id: str) -> None:
        self.db.execute("UPDATE projects SET room_id = ? WHERE slug = ?", (room_id, slug))

    def set_paused(self, slug: str, paused: bool) -> None:
        self.db.execute("UPDATE projects SET paused = ? WHERE slug = ?", (int(paused), slug))

    def update_project(self, slug: str, **fields: str) -> Project:
        assert set(fields) <= {"name", "mode", "model", "effort"}, fields
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE projects SET {columns} WHERE slug = ?", (*fields.values(), slug))
        return self.project(slug)

    def move_project(self, old: str, new: str) -> Project:
        """Give a project a new slug, keeping its jobs."""
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute(
                "INSERT INTO projects (slug, name, summary, mode, model, effort, room_id, paused, created_at)"
                " SELECT ?, name, summary, mode, model, effort, NULL, paused, created_at FROM projects WHERE slug = ?",
                (new, old),
            )
            room = self.db.execute("SELECT room_id FROM projects WHERE slug = ?", (old,)).fetchone()["room_id"]
            self.db.execute("UPDATE jobs SET slug = ? WHERE slug = ?", (new, old))
            self.db.execute("UPDATE cards SET slug = ? WHERE slug = ?", (new, old))
            self.db.execute("DELETE FROM projects WHERE slug = ?", (old,))
            self.db.execute("UPDATE projects SET room_id = ? WHERE slug = ?", (room, new))
        return self.project(new)

    def delete_project(self, slug: str) -> list[int]:
        """Remove a project with its jobs, notes and runs. Returns the job ids (their logs go too)."""
        jobs = "SELECT id FROM jobs WHERE slug = ?"
        with self.db:
            self.db.execute("BEGIN")
            ids = [r["id"] for r in self.db.execute(jobs, (slug,))]
            self.db.execute(f"DELETE FROM notes WHERE job_id IN ({jobs})", (slug,))
            self.db.execute(f"DELETE FROM runs WHERE job_id IN ({jobs})", (slug,))
            self.db.execute(f"DELETE FROM steps WHERE job_id IN ({jobs})", (slug,))
            self.db.execute("DELETE FROM jobs WHERE slug = ?", (slug,))
            self.db.execute("DELETE FROM cards WHERE slug = ?", (slug,))
            self.db.execute("DELETE FROM projects WHERE slug = ?", (slug,))
        return ids

    def live_projects(self) -> list[tuple[Project, str]]:
        """Projects with a site: deployed by a step, or with a finished job (from before steps
        were tracked). With when they last went live, latest first."""
        rows = self.db.execute(
            "SELECT projects.*, MAX(COALESCE(steps.ended_at, jobs.updated_at)) AS live_at FROM projects"
            " JOIN jobs USING (slug) LEFT JOIN steps ON steps.job_id = jobs.id AND steps.kind = 'deploy' AND steps.state = 'done'"
            " WHERE steps.id IS NOT NULL OR jobs.state = 'done' GROUP BY projects.slug ORDER BY live_at DESC"
        )
        found = []
        for r in rows:
            data = dict(r)
            live_at = data.pop("live_at")
            found.append((_project(data), live_at))
        return found

    # --- cards ----------------------------------------------------------------

    def card(self, slug: str) -> Card | None:
        row = self.db.execute("SELECT * FROM cards WHERE slug = ?", (slug,)).fetchone()
        return Card(**{**dict(row), "tags": json.loads(row["tags"])}) if row else None

    def put_card(self, card: Card) -> Card:
        self.db.execute(
            "INSERT OR REPLACE INTO cards (slug, tagline, description, kind, tags, image, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (card.slug, card.tagline, card.description, card.kind, json.dumps(card.tags), card.image, now()),
        )
        return self.card(card.slug)

    # --- jobs -----------------------------------------------------------------

    def add_job(self, slug: str, kind: str, request: str, playbook: str, priority: int = 0) -> Job:
        cur = self.db.execute(
            "INSERT INTO jobs (slug, kind, request, playbook, priority, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, kind, request, playbook, priority, now(), now()),
        )
        return self.job(cur.lastrowid)

    def job(self, job_id: int) -> Job | None:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job(**row) if row else None

    def update_job(self, job_id: int, **fields) -> Job:
        fields["updated_at"] = now()
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))
        return self.job(job_id)

    def active_job(self, slug: str) -> Job | None:
        """The project's unfinished job, if any. A project runs one job at a time."""
        marks = ", ".join("?" * len(ACTIVE))
        row = self.db.execute(
            f"SELECT * FROM jobs WHERE slug = ? AND state IN ({marks}) ORDER BY id LIMIT 1", (slug, *ACTIVE)
        ).fetchone()
        return Job(**row) if row else None

    def jobs(self, states: tuple[str, ...] | None = None, limit: int = 50) -> list[Job]:
        if states:
            marks = ", ".join("?" * len(states))
            rows = self.db.execute(
                f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY priority DESC, id LIMIT ?", (*states, limit)
            )
        else:
            rows = self.db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        return [Job(**r) for r in rows]

    def next_job(self, at: str | None = None) -> Job | None:
        """Highest priority queued job whose project isn't paused; parked jobs come back once their time is up."""
        at = at or now()
        self.db.execute("UPDATE jobs SET state = 'queued', retry_at = NULL WHERE state = 'parked' AND retry_at <= ?", (at,))
        row = self.db.execute(
            "SELECT jobs.* FROM jobs JOIN projects USING (slug)"
            " WHERE jobs.state = 'queued' AND projects.paused = 0"
            " ORDER BY jobs.priority DESC, jobs.id LIMIT 1"
        ).fetchone()
        return Job(**row) if row else None

    def requeue_interrupted(self) -> list[Job]:
        """After a restart, a job left `running` carries on from its current step."""
        jobs = self.jobs(("running",))
        self.db.execute("UPDATE jobs SET state = 'queued' WHERE state = 'running'")
        return jobs

    # --- notes ----------------------------------------------------------------

    def add_note(self, job_id: int, text: str, step_id: int | None = None) -> None:
        self.db.execute(
            "INSERT INTO notes (job_id, text, created_at, step_id) VALUES (?, ?, ?, ?)", (job_id, text, now(), step_id)
        )

    def pending_notes(self, job_id: int, step_id: int | None = None) -> list[tuple[int, str]]:
        """Notes not yet passed on: those for whichever step is next, and those for `step_id`."""
        rows = self.db.execute(
            "SELECT id, text FROM notes WHERE job_id = ? AND consumed = 0 AND (step_id IS NULL OR step_id = ?) ORDER BY id",
            (job_id, step_id),
        )
        return [(r["id"], r["text"]) for r in rows]

    def step_notes(self, step_id: int) -> list[str]:
        rows = self.db.execute("SELECT text FROM notes WHERE step_id = ? ORDER BY id", (step_id,))
        return [r["text"] for r in rows]

    def consume_notes(self, ids: list[int]) -> None:
        self.db.executemany("UPDATE notes SET consumed = 1 WHERE id = ?", [(i,) for i in ids])

    # --- steps ----------------------------------------------------------------

    def add_steps(self, job_id: int, steps: list[Step], after: int | None = None) -> None:
        """Append steps to the job, or insert them after the step at position `after`."""
        with self.db:
            self.db.execute("BEGIN")
            if after is None:
                row = self.db.execute("SELECT MAX(position) AS last FROM steps WHERE job_id = ?", (job_id,)).fetchone()
                after = row["last"] or 0
            else:
                self.db.execute(
                    "UPDATE steps SET position = position + ? WHERE job_id = ? AND position > ?", (len(steps), job_id, after)
                )
            for i, s in enumerate(steps, start=after + 1):
                self.db.execute(
                    "INSERT INTO steps (job_id, position, kind, name, max_turns, optional, state, summary, problem, links)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, i, s.kind, s.name, s.max_turns, int(s.optional), s.state, s.summary, s.problem, json.dumps(s.links)),
                )

    def steps(self, job_id: int) -> list[Step]:
        rows = self.db.execute("SELECT * FROM steps WHERE job_id = ? ORDER BY position", (job_id,))
        return [_step(r) for r in rows]

    def step(self, step_id: int) -> Step | None:
        row = self.db.execute("SELECT * FROM steps WHERE id = ?", (step_id,)).fetchone()
        return _step(row) if row else None

    def update_step(self, step_id: int, **fields) -> Step:
        if "links" in fields:
            fields["links"] = json.dumps(fields["links"])
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE steps SET {columns} WHERE id = ?", (*fields.values(), step_id))
        return self.step(step_id)

    # --- defaults -------------------------------------------------------------

    def defaults(self) -> dict[str, tuple[str, str]]:
        """{step name: (model, effort)}, "" where not set."""
        return {r["name"]: (r["model"], r["effort"]) for r in self.db.execute("SELECT * FROM defaults")}

    def set_default(self, name: str, field: str, value: str) -> None:
        assert field in ("model", "effort"), field
        self.db.execute(
            f"INSERT INTO defaults (name, {field}) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET {field} = excluded.{field}",
            (name, value),
        )

    # --- runs -----------------------------------------------------------------

    def start_run(self, job_id: int, phase: str, model: str, log_path: str, step_id: int | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO runs (job_id, phase, model, started_at, log_path, step_id) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, phase, model, now(), log_path, step_id),
        )
        return cur.lastrowid

    def finish_run(self, run_id: int, outcome: str, **usage) -> None:
        fields = {"outcome": outcome, "ended_at": now(), **usage}
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE runs SET {columns} WHERE id = ?", (*fields.values(), run_id))

    def run(self, run_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def finished_runs(self, since: str = "") -> list[dict]:
        """Runs that ended, started at or after `since`, with their project and step."""
        rows = self.db.execute(
            "SELECT runs.*, jobs.slug, steps.name AS step_name FROM runs JOIN jobs ON jobs.id = runs.job_id"
            " LEFT JOIN steps ON steps.id = runs.step_id WHERE runs.ended_at IS NOT NULL AND runs.started_at >= ?"
            " ORDER BY runs.id",
            (since,),
        )
        return [dict(r) for r in rows]

    def finished_steps(self, since: str = "") -> list[tuple[Step, str]]:
        """Steps that ran and ended at or after `since`, with their project's slug."""
        rows = self.db.execute(
            "SELECT steps.*, jobs.slug FROM steps JOIN jobs ON jobs.id = steps.job_id"
            " WHERE steps.started_at IS NOT NULL AND steps.ended_at >= ? ORDER BY steps.ended_at",
            (since,),
        )
        found = []
        for r in rows:
            data = dict(r)
            slug = data.pop("slug")
            found.append((_step(data), slug))
        return found

    def runs(self, job_id: int | None = None, limit: int = 50) -> list[dict]:
        if job_id is None:
            rows = self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self.db.execute("SELECT * FROM runs WHERE job_id = ? ORDER BY id", (job_id,))
        return [dict(r) for r in rows]


def _step(row: sqlite3.Row | dict) -> Step:
    data = dict(row)
    data["links"] = json.loads(data["links"] or "{}")
    data["optional"] = bool(data["optional"])
    return Step(**data)


def _project(row: sqlite3.Row | dict) -> Project:
    data = dict(row)
    data["paused"] = bool(data["paused"])
    return Project(**data)
