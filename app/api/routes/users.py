from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api import deps
from app.crud.user import user as crud_user
from app.schemas.user import UserCreate, UserPublic, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", response_model=list[UserPublic])
def list_users(skip: int = 0, limit: int = 100, db: Session = Depends(deps.get_db)):
    return crud_user.get_users(db, skip=skip, limit=limit)


@router.post("/", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def create_user(user_in: UserCreate, db: Session = Depends(deps.get_db)):
    if crud_user.get_user_by_email(db, user_in.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )
    return crud_user.create_user(db, user_in)


@router.get("/{user_id}", response_model=UserPublic)
def get_user(user_id: int, db: Session = Depends(deps.get_db)):
    user = crud_user.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )
    return user


@router.put("/{user_id}", response_model=UserPublic)
def update_user(user_id: int, user_in: UserUpdate, db: Session = Depends(deps.get_db)):
    user = crud_user.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )
    return crud_user.update_user(db, user, user_in)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(deps.get_db)):
    user = crud_user.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )
    crud_user.delete_user(db, user)
