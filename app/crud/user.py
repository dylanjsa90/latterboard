import hashlib
from collections.abc import Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.models.user_avatar import UserAvatar
from app.schemas import UserCreate, UserUpdate
from app.schemas.user import ProfileUpdate

from .base import CRUDBase


class CRUDUser(CRUDBase[User, UserCreate, UserUpdate]):
    def get_user(self, db: Session, user_id: int) -> User | None:
        return db.get(self.model, user_id)

    def get_user_by_email(self, db: Session, email: str) -> User | None:
        return db.query(self.model).filter(self.model.email == email).first()

    def get_user_by_username(self, db: Session, username: str) -> User | None:
        # Case-insensitive, so "Maya" counts as taken once "maya" exists.
        return (
            db.query(self.model)
            .filter(func.lower(self.model.username) == username.lower())
            .first()
        )

    def get_players(self, db: Session, usernames: Sequence[str]) -> list[User]:
        wanted = {name.lower() for name in usernames}
        if not wanted:
            return []
        return (
            db.query(self.model)
            .filter(func.lower(self.model.username).in_(wanted))
            .all()
        )

    def get_users(self, db: Session, skip: int = 0, limit: int = 100) -> list[User]:
        return db.query(self.model).offset(skip).limit(limit).all()

    def create_user(self, db: Session, user_in: UserCreate) -> User:
        user = User(  # type: ignore
            email=user_in.email,
            username=user_in.username,
            hashed_password=get_password_hash(user_in.password),
            display_name=user_in.display_name,
            birth_year=user_in.birth_year,
            location=user_in.location,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    def update_user(self, db: Session, user: User, user_in: UserUpdate) -> User:
        update_data = user_in.model_dump(exclude_unset=True)
        if "password" in update_data:
            update_data["hashed_password"] = get_password_hash(
                update_data.pop("password")
            )
        for field, value in update_data.items():
            setattr(user, field, value)
        db.commit()
        db.refresh(user)
        return user

    def update_profile(self, db: Session, user: User, profile_in: ProfileUpdate) -> User:
        for field, value in profile_in.model_dump(exclude_unset=True).items():
            # Name, handle, and birth year can change but never be cleared; location can.
            if value is None and field != "location":
                continue
            setattr(user, field, value)
        db.commit()
        db.refresh(user)
        return user

    def get_avatar(self, db: Session, user_id: int) -> UserAvatar | None:
        return db.get(UserAvatar, user_id)

    def set_avatar(self, db: Session, user: User, image: bytes, content_type: str) -> User:
        # Written through the table, not `user.avatar`: touching the relationship would
        # SELECT the old image bytes we're about to replace, which is what keeping
        # photos out of `user` was meant to avoid.
        updated = (
            db.query(UserAvatar)
            .filter(UserAvatar.user_id == user.id)
            .update(
                {UserAvatar.image: image, UserAvatar.content_type: content_type},
                synchronize_session=False,
            )
        )
        if not updated:
            db.add(
                UserAvatar(  # type: ignore[call-arg]
                    user_id=user.id, image=image, content_type=content_type
                )
            )
        user.avatar_version = hashlib.sha256(image).hexdigest()[:12]
        db.commit()
        db.refresh(user)
        return user

    def remove_avatar(self, db: Session, user: User) -> None:
        # Deleted by query for the same reason `set_avatar` updates by query.
        db.query(UserAvatar).filter(UserAvatar.user_id == user.id).delete(
            synchronize_session=False
        )
        user.avatar_version = None
        db.commit()

    def delete_user(self, db: Session, user: User) -> User | None:
        obj = self.get(db, user.id)
        if obj is not None:
            db.delete(obj)
            db.commit()

        return obj

    def authenticate(self, db: Session, email: str, password: str) -> User | None:
        db_user = self.get_user_by_email(db=db, email=email)
        if not db_user:
            return None
        verified, updated_password_hash = verify_password(
            password, db_user.hashed_password
        )
        if not verified:
            return None
        if updated_password_hash:
            db_user.hashed_password = updated_password_hash
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user


user = CRUDUser(User)
