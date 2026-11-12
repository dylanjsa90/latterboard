from loguru import logger
from sqlalchemy import Engine, text
from tenacity import retry, stop_after_attempt, wait_fixed

from app.database import engine

max_tries = 60 * 5  # 5 minutes
wait_seconds = 1


@retry(stop=stop_after_attempt(max_tries), wait=wait_fixed(wait_seconds))
def init(db_engine: Engine) -> None:
    try:
        with db_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning("Database not ready yet: {}", e)
        raise


def main() -> None:
    logger.info("Waiting for the database")
    init(engine)
    logger.info("Database is ready")


if __name__ == "__main__":
    main()
