from pydantic import BaseModel


class TopicPresence(BaseModel):
    topic: str
    connections: int


class TopicUsernames(BaseModel):
    topic: str
    usernames: list[str]
