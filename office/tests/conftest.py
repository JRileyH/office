from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from office.bot import Bot
from office.config import Settings
from office.harness import FakeHarness
from office.matrix import Message
from office.prompts import Prompts
from office.scheduler import Scheduler
from office.store import Store
from office.workspace import LocalWorkspace

REPO = Path(__file__).resolve().parents[2]
OWNER = "@riggy:matrix.unlaunched.dev"


class FakeChat:
    """Records what office says; rooms are numbered."""

    def __init__(self):
        self.sent: list[tuple[str, str, bool]] = []
        self.rooms: list[dict] = []
        self.typing_calls: list[tuple[str, bool]] = []
        self.names: dict[str, str] = {}
        self.topics: dict[str, str] = {}
        self.aliases: list[tuple[str, str]] = []


    async def send(self, room_id: str, text: str, notice: bool = False) -> str:
        self.sent.append((room_id, text, notice))
        return f"$event{len(self.sent)}"

    async def typing(self, room_id: str, on: bool) -> None:
        self.typing_calls.append((room_id, on))

    async def create_room(self, name: str, topic: str, alias: str | None = None) -> str:
        room_id = f"!room{len(self.rooms) + 1}:matrix.unlaunched.dev"
        self.rooms.append({"id": room_id, "name": name, "topic": topic, "alias": alias})
        return room_id

    async def set_name(self, room_id: str, name: str) -> None:
        self.names[room_id] = name

    async def set_topic(self, room_id: str, topic: str) -> None:
        self.topics[room_id] = topic

    async def add_alias(self, room_id: str, alias: str) -> None:
        self.aliases.append((room_id, alias))

    def texts(self, room_id: str) -> list[str]:
        return [text for room, text, _ in self.sent if room == room_id]


class FakeDeployer:
    def __init__(self):
        self.stopped: list[str] = []

    async def stop(self, slug: str) -> None:
        self.stopped.append(slug)


class Office:
    """The bot and scheduler wired to fakes, as in production."""

    def __init__(self, tmp: Path, script=None, **overrides):
        self.settings = replace(
            Settings(
                base_domain="unlaunched.dev",
                edge_port="",
                harness="claude",
                org="unlaunched",
                matrix_url="http://tuwunel:8008",
                matrix_user="@office:matrix.unlaunched.dev",
                matrix_password="secret",
                owner=OWNER,
                deployer_url="http://deployer:8000",
                state_dir=tmp / "state",
                workspace_dir=tmp / "workspace",
                process_dir=REPO,
            ),
            **overrides,
        )
        self.settings.state_dir.mkdir(parents=True)
        self.store = Store(self.settings.state_dir / "office.db")
        self.chat = FakeChat()
        self.harness = FakeHarness(script)
        self.workspace = LocalWorkspace(self.settings.workspace_dir)
        self.prompts = Prompts(self.settings)
        self.scheduler = Scheduler(self.settings, self.store, self.chat, self.harness, self.workspace, self.prompts)
        self.deployer = FakeDeployer()
        self.bot = Bot(
            self.settings, self.store, self.chat, self.scheduler, self.harness, self.workspace, self.prompts, self.deployer
        )
        self.bot.start_delay = timedelta(0)  # no grace period in tests

    async def say(self, room_id: str, body: str) -> None:
        await self.bot.handle(Message(room_id, OWNER, body, f"$in{len(self.chat.sent)}"))

    async def step(self) -> None:
        """One scheduler turn: the next job's current phase."""
        job = self.store.next_job()
        assert job is not None, "nothing queued"
        await self.scheduler.step(job)

    @property
    def inbox(self) -> str:
        return self.store.get("inbox_room")


@pytest.fixture
def office(tmp_path):
    return Office(tmp_path)
