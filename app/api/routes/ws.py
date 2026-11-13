"""The live websocket and the presence routes about it.

`router` holds the REST presence lookups (mounted under /api/v1); `socket_router`
holds the socket itself, which `app/main.py` mounts at the root: `/ws/{game_name}`.
"""

import json
import re

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from loguru import logger

from app.api import deps
from app.connection_manager import manager
from app.crud.vocab import crud_vocab
from app.database import SessionLocal
from app.models import Match, User
from app.schemas.ws import TopicPresence, TopicUsernames

router = APIRouter(prefix="/ws", tags=["ws"])
socket_router = APIRouter()


@router.get("/{game_name}/connections", response_model=TopicPresence)
async def topic_connections(game_name: str) -> TopicPresence:
    """How many websocket clients are currently connected to a game's topic."""
    return TopicPresence(topic=game_name, connections=await manager.count(game_name))


@router.get("/{game_name}/usernames", response_model=TopicUsernames)
async def topic_usernames(game_name: str) -> TopicUsernames:
    """Which usernames are currently connected to a game's topic."""
    return TopicUsernames(topic=game_name, usernames=await manager.usernames(game_name))


def _is_match_participant(user_id: int, match_id: int) -> bool:
    db = SessionLocal()
    try:
        match = db.get(Match, match_id)
        return match is not None and user_id in (match.inviter_id, match.invitee_id)
    finally:
        db.close()


_ROOM_CODE = re.compile(r"[A-Fa-f0-9]{6}")


def _room_topic(code: object) -> str | None:
    """The socket topic for a vocab room code from a client frame, or None if malformed."""
    if isinstance(code, str) and _ROOM_CODE.fullmatch(code):
        return f"vocab:{code.upper()}"
    return None


def _is_room_participant(user_id: int, topic: str) -> bool:
    db = SessionLocal()
    try:
        return crud_vocab.is_participant(db, topic.removeprefix("vocab:"), str(user_id))
    finally:
        db.close()


@socket_router.websocket("/ws/{game_name}")
async def websocket_endpoint(
    websocket: WebSocket,
    game_name: str,
    current_user: User = Depends(deps.get_current_user_ws),
) -> None:
    await manager.connect(websocket, game_name, current_user.username)
    await manager.join(websocket, f"user:{current_user.id}", current_user.username)
    try:
        await websocket.send_json(
            {"type": "connected", "topic": game_name, "username": current_user.username}
        )
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except ValueError:
                frame = None
            msg_type = frame.get("type") if isinstance(frame, dict) else None

            if msg_type == "join_match":
                match_id = frame.get("match_id")
                if isinstance(match_id, int) and await run_in_threadpool(
                    _is_match_participant, current_user.id, match_id
                ):
                    await manager.join(
                        websocket, f"match:{match_id}", current_user.username
                    )
                    await websocket.send_json(
                        {"type": "joined_match", "match_id": match_id}
                    )
            elif msg_type == "leave_match":
                match_id = frame.get("match_id")
                if isinstance(match_id, int):
                    await manager.disconnect(websocket, f"match:{match_id}")
            elif msg_type == "join_room":
                topic = _room_topic(frame.get("code"))
                if topic is not None and await run_in_threadpool(
                    _is_room_participant, current_user.id, topic
                ):
                    await manager.join(websocket, topic, current_user.username)
                    await websocket.send_json(
                        {"type": "joined_room", "code": topic.removeprefix("vocab:")}
                    )
            elif msg_type == "leave_room":
                topic = _room_topic(frame.get("code"))
                if topic:
                    await manager.disconnect(websocket, topic)
            else:
                await manager.broadcast(
                    {
                        "type": "message",
                        "topic": game_name,
                        "username": current_user.username,
                        "data": raw,
                    },
                    topic=game_name,
                )
    except WebSocketDisconnect:
        logger.info("Websocket left topic {} ({})", game_name, current_user.username)
    finally:
        await manager.disconnect_all(websocket)
