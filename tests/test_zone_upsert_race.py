"""Duplicate zone rows drop the socket. The safe upsert flag keeps one row."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _zone(last_seen):
    import main
    return main.R2CZoneState(
        organization_id="org-race",
        map_id="MAP",
        zone_id="same-zone",
        guid="same-zone",
        name="same",
        last_seen_ms=last_seen,
    )


class ZoneUpsertRaceTest(unittest.TestCase):
    def test_duplicate_rows_raise_until_the_conflict_upsert_is_enabled(self):
        asyncio.run(self._run())

    async def _run(self):
        import main

        directory = tempfile.mkdtemp(prefix="r2c-zone-race-")
        path = Path(directory) / "race.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        sessions = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        previous = main.AsyncSessionLocal
        main.AsyncSessionLocal = sessions
        os.environ.pop("R2C_SAFE_ZONE_UPSERT", None)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(main.Base.metadata.create_all)
            async with sessions() as session:
                session.add(_zone(1))
                session.add(_zone(2))
                await session.commit()
            with self.assertRaises(Exception) as caught:
                await main.r2c_hub._upsert_zone_state(
                    "org-race", "MAP", "same-zone", "same-zone", "same",
                    1.0, 2.0, 0, True, 3,
                )
            self.assertIn("Multiple rows", str(caught.exception))

            os.environ["R2C_SAFE_ZONE_UPSERT"] = "1"
            async with engine.begin() as conn:
                await main._ensure_unique_zone_scope(conn)
            await asyncio.gather(*[
                main.r2c_hub._upsert_zone_state(
                    "org-race", "MAP", "same-zone", f"guid-{index}", "same",
                    1.0, 2.0, 0, True, 100 + index,
                )
                for index in range(8)
            ])
            async with sessions() as session:
                rows = (await session.scalars(select(main.R2CZoneState).where(
                    main.R2CZoneState.organization_id == "org-race",
                ))).all()
            self.assertEqual(1, len(rows))
            self.assertGreaterEqual(rows[0].last_seen_ms, 100)
        finally:
            main.AsyncSessionLocal = previous
            os.environ.pop("R2C_SAFE_ZONE_UPSERT", None)
            await engine.dispose()

    def test_upload_cap_rejects_a_declared_oversize_body(self):
        import main

        class Request:
            headers = {"content-length": str(main.MAX_ARCHIVE_UPLOAD_BYTES + 1)}

            async def stream(self):
                yield b"unused"

        with self.assertRaises(main.HTTPException) as caught:
            asyncio.run(main._read_capped_body(Request(), main.MAX_ARCHIVE_UPLOAD_BYTES))
        self.assertEqual(413, caught.exception.status_code)
