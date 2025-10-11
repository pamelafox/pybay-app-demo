import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypedDict

import fastapi
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fastapi_app.dependencies import (
    create_async_sessionmaker,
    get_azure_credential,
)
from fastapi_app.postgres_engine import create_postgres_engine_from_env

logger = logging.getLogger("ragapp")


class State(TypedDict):
    sessionmaker: async_sessionmaker[AsyncSession]


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI) -> AsyncIterator[State]:
    azure_credential = None
    if os.getenv("POSTGRES_HOST", "").endswith(".database.azure.com"):
        azure_credential = await get_azure_credential()
    engine = await create_postgres_engine_from_env(azure_credential)
    sessionmaker = await create_async_sessionmaker(engine)
    yield {"sessionmaker": sessionmaker}
    await engine.dispose()


def create_app(testing: bool = False):
    if not testing:
        load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO)

    app = fastapi.FastAPI(docs_url="/docs", lifespan=lifespan)

    from fastapi_app.api import routes

    app.include_router(routes.router)

    return app
