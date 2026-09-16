from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.api import deps
from app.crud.user import user as crud_user
from app.lib.avatar import (
    AVATAR_CONTENT_TYPE,
    AVATAR_MAX_BYTES,
    InvalidImage,
    process_avatar,
)
from app.models import User
from app.schemas.user import (
    PlayerPublic,
    ProfileUpdate,
    UserCreate,
    UserPrivate,
    UserPublic,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["users"])

PLAYERS_LOOKUP_MAX = 50
USERNAME_TAKEN = "That username is taken."


@router.get("/", response_model=list[UserPublic])
def list_users(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(deps.get_db),
    _: User = Depends(deps.get_current_user),
):
    return crud_user.get_users(db, skip=skip, limit=limit)


@router.post("/", response_model=UserPrivate, status_code=status.HTTP_201_CREATED)
def create_user(user_in: UserCreate, db: Session = Depends(deps.get_db)):
    if crud_user.get_user_by_email(db, user_in.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )
    if crud_user.get_user_by_username(db, user_in.username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=USERNAME_TAKEN)
    return crud_user.create_user(db, user_in)


# The literal paths below must stay above `/{user_id}`, which would otherwise claim
# them and reject "players" / "me" as a non-integer id.


@router.get("/players", response_model=list[PlayerPublic])
def get_players(
    usernames: list[str] = Query(default=[]),
    db: Session = Depends(deps.get_db),
    _: User = Depends(deps.get_current_user),
) -> list[User]:
    """Public player cards for up to 50 handles. Unknown handles are left out."""
    return crud_user.get_players(db, usernames[:PLAYERS_LOOKUP_MAX])


@router.patch("/me", response_model=UserPrivate)
def update_me(
    profile_in: ProfileUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> User:
    if profile_in.username is not None:
        owner = crud_user.get_user_by_username(db, profile_in.username)
        if owner is not None and owner.id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=USERNAME_TAKEN
            )
    return crud_user.update_profile(db, current_user, profile_in)


@router.put("/me/avatar", response_model=UserPrivate)
def upload_avatar(
    file: UploadFile,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> User:
    # One byte past the cap is enough to know a file is too big without reading it all.
    data = file.file.read(AVATAR_MAX_BYTES + 1)
    if len(data) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Photos can be up to 2 MB.")
    try:
        image = process_avatar(data)
    except InvalidImage as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return crud_user.set_avatar(db, current_user, image, AVATAR_CONTENT_TYPE)


@router.delete("/me/avatar", status_code=status.HTTP_204_NO_CONTENT)
def remove_avatar(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> None:
    crud_user.remove_avatar(db, current_user)


@router.get(
    "/{user_id}/avatar",
    response_class=Response,
    responses={200: {"content": {AVATAR_CONTENT_TYPE: {}}}},
)
def get_avatar(user_id: int, db: Session = Depends(deps.get_db)) -> Response:
    """Public, since `<img>` can't send a bearer token. The URL carries `?v=<hash>`, so
    a new photo gets a new URL and each one can be cached for good."""
    avatar = crud_user.get_avatar(db, user_id)
    if avatar is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This player has no photo."
        )
    return Response(
        content=avatar.image,
        media_type=avatar.content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/{user_id}", response_model=UserPublic)
def get_user(
    user_id: int,
    db: Session = Depends(deps.get_db),
    _: User = Depends(deps.get_current_user),
):
    user = crud_user.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )
    return user


@router.put("/{user_id}", response_model=UserPublic)
def update_user(
    user_id: int,
    user_in: UserUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    user = crud_user.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )
    if current_user.id != user_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions."
        )
    return crud_user.update_user(db, user, user_in)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    user = crud_user.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )
    if current_user.id != user_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions."
        )
    crud_user.delete_user(db, user)
