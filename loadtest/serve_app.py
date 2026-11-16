"""Child process entry. Local harness only; not used by Cloud Run."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn


def main() -> None:
    log_dir = os.environ.get("R2C_LOADTEST_LOG_DIR", "").strip()
    import main as tracker

    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        tracker.BASE_LOG_DIRECTORY = log_dir
    if os.environ.get("R2C_LOADTEST_STUB_WEATHER", "").strip() == "1":
        def stub_weather(*_args, **_kwargs):
            return {
                "temp": 0.0,
                "hum": 0.0,
                "precip": 0.0,
                "dew": 0.0,
                "wind": 0.0,
                "gusts": 0.0,
                "cloud": 0.0,
            }

        tracker.get_weather = stub_weather
    uvicorn.run(
        tracker.app,
        host="127.0.0.1",
        port=int(os.environ["PORT"]),
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
