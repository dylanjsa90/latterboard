from fastapi import APIRouter

from app.connection_manager import manager
from app.schemas.ws import TopicPresence, TopicUsernames

router = APIRouter(prefix="/ws", tags=["ws"])


@router.get("/{game_name}/connections", response_model=TopicPresence)
async def topic_connections(game_name: str):
    """How many websocket clients are currently connected to a game's topic."""
    return TopicPresence(topic=game_name, connections=await manager.count(game_name))


@router.get("/{game_name}/usernames", response_model=TopicUsernames)
async def topic_usernames(game_name: str):
    """Which usernames are currently connected to a game's topic."""
    return TopicUsernames(topic=game_name, usernames=await manager.usernames(game_name))
