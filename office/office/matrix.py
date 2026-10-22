"""A small Matrix client: just what the bot needs, over the client-server API.

Only messages from the owner are acted on; everyone else is ignored (federation and
registration are off, so in practice there is no one else).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import httpx

from .store import Store

log = logging.getLogger(__name__)

DEVICE_ID = "OFFICE_BOT"
SYNC_TIMEOUT_MS = 30_000
# Only what the bot reacts to: messages and invites (invites always come through).
SYNC_FILTER = json.dumps(
    {
        "presence": {"types": []},
        "account_data": {"types": []},
        "room": {
            "timeline": {"types": ["m.room.message"], "limit": 50},
            "state": {"types": [], "lazy_load_members": True},
            "ephemeral": {"types": []},
            "account_data": {"types": []},
        },
    }
)


@dataclass(frozen=True)
class Message:
    room_id: str
    sender: str
    body: str
    event_id: str


class MatrixError(Exception):
    pass


class Chat(Protocol):
    """What the bot and scheduler use; `Matrix` here, a recorder in tests."""

    async def send(self, room_id: str, text: str, notice: bool = False) -> str: ...
    async def typing(self, room_id: str, on: bool) -> None: ...
    async def create_room(self, name: str, topic: str, alias: str | None = None) -> str: ...


class Matrix:
    def __init__(self, url: str, user: str, password: str, owner: str, store: Store, client: httpx.AsyncClient | None = None):
        self.user = user
        self.password = password
        self.owner = owner
        self.store = store
        self.client = client or httpx.AsyncClient(base_url=url, timeout=SYNC_TIMEOUT_MS / 1000 + 30)
        self.token = store.get("matrix_token")

    async def login(self) -> None:
        if self.token:
            try:
                await self._call("GET", "/_matrix/client/v3/account/whoami", relogin=False)
                return
            except MatrixError:
                self.token = None
        r = await self.client.post(
            "/_matrix/client/v3/login",
            json={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": self.user},
                "password": self.password,
                "device_id": DEVICE_ID,
                "initial_device_display_name": "office bot",
            },
        )
        if r.status_code != 200:
            raise MatrixError(f"login as {self.user} failed: {r.status_code} {_errcode(r)}")
        self.token = r.json()["access_token"]
        self.store.put("matrix_token", self.token)

    async def _call(self, method: str, path: str, relogin: bool = True, **kwargs) -> dict:
        r = await self.client.request(method, path, headers={"Authorization": f"Bearer {self.token}"}, **kwargs)
        if r.status_code == 401 and relogin:
            self.token = None
            await self.login()
            return await self._call(method, path, relogin=False, **kwargs)
        if r.status_code != 200:
            raise MatrixError(f"{method} {path.split('?')[0]}: {r.status_code} {_errcode(r)}")
        return r.json()

    # --- sending --------------------------------------------------------------

    async def send(self, room_id: str, text: str, notice: bool = False) -> str:
        """m.text notifies Riggy's phone; m.notice (progress, acknowledgements) doesn't by default."""
        content = {"msgtype": "m.notice" if notice else "m.text", "body": text}
        path = f"/_matrix/client/v3/rooms/{quote(room_id)}/send/m.room.message/{uuid.uuid4().hex}"
        return (await self._call("PUT", path, json=content))["event_id"]

    async def typing(self, room_id: str, on: bool) -> None:
        path = f"/_matrix/client/v3/rooms/{quote(room_id)}/typing/{quote(self.user)}"
        body = {"typing": True, "timeout": 120_000} if on else {"typing": False}
        try:
            await self._call("PUT", path, json=body)
        except MatrixError as e:
            log.warning("typing: %s", e)

    async def create_room(self, name: str, topic: str, alias: str | None = None) -> str:
        """A private room with the owner invited and made admin."""
        body = {
            "preset": "private_chat",
            "name": name,
            "topic": topic,
            "invite": [self.owner],
            # The bot, as creator, outranks everyone (room v12 rejects listing creators here).
            "power_level_content_override": {"users": {self.owner: 100}},
        }
        if alias:
            body["room_alias_name"] = alias
        try:
            return (await self._call("POST", "/_matrix/client/v3/createRoom", json=body))["room_id"]
        except MatrixError:
            if not alias:
                raise
            # Alias taken (say, by an archived project's room): the room matters, the alias doesn't.
            del body["room_alias_name"]
            return (await self._call("POST", "/_matrix/client/v3/createRoom", json=body))["room_id"]

    async def set_topic(self, room_id: str, topic: str) -> None:
        await self._call("PUT", f"/_matrix/client/v3/rooms/{quote(room_id)}/state/m.room.topic", json={"topic": topic})

    async def join(self, room_id: str) -> None:
        await self._call("POST", f"/_matrix/client/v3/join/{quote(room_id)}", json={})

    # --- receiving ------------------------------------------------------------

    async def sync_forever(self, handle: Callable[[Message], Awaitable[None]]) -> None:
        since = self.store.get("matrix_since")
        if since is None:
            # First start: skip whatever history exists and act only on new messages.
            data = await self._call("GET", "/_matrix/client/v3/sync", params={"filter": SYNC_FILTER, "timeout": 0})
            since = data["next_batch"]
            self.store.put("matrix_since", since)
        while True:
            try:
                data = await self._call(
                    "GET",
                    "/_matrix/client/v3/sync",
                    params={"filter": SYNC_FILTER, "since": since, "timeout": SYNC_TIMEOUT_MS},
                )
            except (MatrixError, httpx.HTTPError) as e:
                log.warning("sync failed, retrying: %s", e)
                await asyncio.sleep(5)
                continue
            for message in self.messages(data):
                try:
                    await handle(message)
                except Exception:
                    log.exception("handling %s in %s", message.event_id, message.room_id)
            await self.accept_invites(data)
            since = data["next_batch"]
            self.store.put("matrix_since", since)

    def messages(self, data: dict) -> list[Message]:
        found = []
        for room_id, room in data.get("rooms", {}).get("join", {}).items():
            for event in room.get("timeline", {}).get("events", []):
                content = event.get("content", {})
                if (
                    event.get("type") == "m.room.message"
                    and event.get("sender") == self.owner
                    and content.get("msgtype") == "m.text"
                    and isinstance(content.get("body"), str)
                    and "m.relates_to" not in content  # edits and replacements
                ):
                    found.append(Message(room_id, event["sender"], content["body"].strip(), event["event_id"]))
        return found

    async def accept_invites(self, data: dict) -> None:
        """Join rooms the owner invites the bot to (a DM from the phone, say); ignore anyone else."""
        for room_id, room in data.get("rooms", {}).get("invite", {}).items():
            inviter = next(
                (
                    e.get("sender")
                    for e in room.get("invite_state", {}).get("events", [])
                    if e.get("type") == "m.room.member" and e.get("state_key") == self.user
                ),
                None,
            )
            if inviter == self.owner:
                try:
                    await self.join(room_id)
                except MatrixError as e:
                    log.warning("joining %s: %s", room_id, e)


def _errcode(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return ""
    return " ".join(str(body.get(k, "")) for k in ("errcode", "error")).strip()
