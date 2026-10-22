"""Phase 1 check: DM a user from the office account, to prove push notifications.

    python -m office.matrix_ping @riggy:matrix.unlaunched.dev [message]

The first run creates a direct chat and invites the user; later runs reuse it.
Replaced by the real bot in Phase 2.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HOMESERVER = os.environ.get("MATRIX_URL", "http://tuwunel:8008")
ROOM_FILE = Path("/var/lib/office/ping-room")


def call(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(HOMESERVER + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    to = sys.argv[1]
    text = " ".join(sys.argv[2:]) or "hello from office"

    login = call(
        "POST",
        "/_matrix/client/v3/login",
        {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": os.environ["OFFICE_MATRIX_USER"]},
            "password": os.environ["OFFICE_MATRIX_PASSWORD"],
            "device_id": "OFFICE_PING",
            "initial_device_display_name": "office ping",
        },
    )
    token = login["access_token"]

    if ROOM_FILE.exists():
        room_id = ROOM_FILE.read_text().strip()
    else:
        room = call(
            "POST",
            "/_matrix/client/v3/createRoom",
            {"is_direct": True, "invite": [to], "preset": "trusted_private_chat", "name": "office"},
            token,
        )
        room_id = room["room_id"]
        ROOM_FILE.write_text(room_id)
        print(f"created {room_id} and invited {to}; accept the invite, then run this again")

    path = f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/send/m.room.message/{uuid.uuid4().hex}"
    event = call("PUT", path, {"msgtype": "m.text", "body": text}, token)
    print(f"sent {event['event_id']} to {room_id}")


if __name__ == "__main__":
    main()
