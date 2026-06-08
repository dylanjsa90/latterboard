from sqlalchemy.orm import Session

from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.schemas import UserCreate, UserUpdate

from .base import CRUDBase


class CRUDUser(CRUDBase[User, UserCreate, UserUpdate]):

  def get_user(self, db: Session, user_id: int) -> User | None:
    return db.get(self.model, user_id)

  def get_user_by_email(self, db: Session, email: str) -> User | None:
    return db.query(self.model).filter(self.model.email == email).first()
    
  def get_users(self, db: Session, skip: int = 0, limit: int = 100) -> list[User]:
    return db.query(self.model).offset(skip).limit(limit).all()


  def create_user(self, db: Session, user_in: UserCreate) -> User:
    user = User( # type: ignore
      email=user_in.email,
      username=user_in.username,
      hashed_password=get_password_hash(user_in.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
    
  def update_user(self, db: Session, user: User, user_in: UserUpdate) -> User:
    update_data = user_in.model_dump(exclude_unset=True)
    if "password" in update_data:
      update_data["hashed_password"] = get_password_hash(update_data.pop("password"))
    for field, value in update_data.items():
      setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user

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
    verified, updated_password_hash = verify_password(password, db_user.hashed_password)
    if not verified:
      return None
    if updated_password_hash:
      db_user.hashed_password = updated_password_hash
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

user = CRUDUser(User)








