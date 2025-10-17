"""Add test species for coverage testing."""

import asyncio
import os
import sys

sys.path.insert(0, "src/backend")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fastapi_app.postgres_models import Species

os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_USERNAME"] = "admin"
os.environ["POSTGRES_DATABASE"] = os.getenv("POSTGRES_DATABASE", "observations_test")
os.environ["POSTGRES_PASSWORD"] = "postgres"
os.environ["POSTGRES_SSL"] = "prefer"


async def add_test_species():
    engine = create_async_engine(f"postgresql+asyncpg://admin:postgres@localhost/{os.environ['POSTGRES_DATABASE']}")
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        await session.begin()
        existing = (await session.scalars(select(Species).where(Species.taxon_id == 888888))).first()
        if existing:
            await session.delete(existing)
        sp1 = Species(
            taxon_id=888888,
            scientific_name="Test Incomplete Arrays",
            common_name="Test Incomplete",
            phenology_counts=[1, 2],
            phenology_normalized=[0.5, 0.5],
            phenology_counts_all=[3, 4, 5],
            phenology_normalized_all=[0.3, 0.4, 0.3],
            peak_month=5,
            peak_month_all=6,
            window_start=4,
            window_end=6,
            window_start_all=5,
            window_end_all=7,
        )
        session.add(sp1)
        existing2 = (await session.scalars(select(Species).where(Species.taxon_id == 777777))).first()
        if existing2:
            await session.delete(existing2)
        counts_large = [1500000, 2500, 500, 100, 50, 20, 10, 5, 3, 2, 1, 0]
        norm_large = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01]
        sp2 = Species(
            taxon_id=777777,
            scientific_name="Test Large Counts",
            common_name="Test Large",
            phenology_counts=counts_large,
            phenology_normalized=norm_large,
            phenology_counts_all=counts_large,
            phenology_normalized_all=norm_large,
            peak_month=1,
            peak_month_all=1,
            window_start=1,
            window_end=3,
            window_start_all=1,
            window_end_all=3,
        )
        session.add(sp2)
        await session.commit()
        print("Test species added successfully")


if __name__ == "__main__":
    asyncio.run(add_test_species())
