"""F1 capstone solution entry point (starts the service)."""
from __future__ import annotations

from . import config


def main() -> None:
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()
