from __future__ import annotations

import argparse
import json

from .private_backup import restore_backup


def main(argv=None):
    parser = argparse.ArgumentParser(description="Restore a verified Hub private backup into a separate destination")
    parser.add_argument("archive")
    parser.add_argument("destination")
    args = parser.parse_args(argv)
    print(json.dumps(restore_backup(args.archive, args.destination), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
