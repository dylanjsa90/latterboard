from pydantic import BaseModel


class TopicPresence(BaseModel):
    topic: str
    connections: int
