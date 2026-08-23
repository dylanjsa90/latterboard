from fastapi import APIRouter

from app.connection_manager import manager
from app.schemas.ws import TopicPresence

router = APIRouter(prefix="/ws", tags=["ws"])


@router.get("/{game_name}/connections", response_model=TopicPresence)
async def topic_connections(game_name: str):
    """How many websocket clients are currently connected to a game's topic."""
    return TopicPresence(topic=game_name, connections=await manager.count(game_name))
