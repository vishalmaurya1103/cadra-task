"""One-command reproducible data preparation. Never edits source files."""
from __future__ import annotations

import json

from .data import prepare_index, write_index


def main() -> None:
    index, report = prepare_index()
    index_path, report_path = write_index(index, report)
    print(f"wrote {index_path}")
    print(f"wrote {report_path}")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
