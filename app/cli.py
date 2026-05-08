"""Operator CLI. Run inside the container, e.g.:

    docker compose exec shelf python -m app.cli reset-password admin
"""
import argparse
import getpass
import sys

from app.auth import hash_password
from app.database import get_db


def cmd_reset_password(args: argparse.Namespace) -> int:
    with get_db() as db:
        row = db.execute(
            "SELECT id FROM users WHERE username = ?", (args.username,)
        ).fetchone()
        if not row:
            print(f"error: no user with username '{args.username}'", file=sys.stderr)
            return 1

        pw1 = getpass.getpass(f"New password for '{args.username}': ")
        if not pw1:
            print("error: password cannot be empty", file=sys.stderr)
            return 1
        pw2 = getpass.getpass("Confirm: ")
        if pw1 != pw2:
            print("error: passwords do not match", file=sys.stderr)
            return 1

        db.execute(
            "UPDATE users SET password = ?, token_version = token_version + 1, "
            "updated_at = datetime('now') WHERE id = ?",
            (hash_password(pw1), row["id"]),
        )

    print(f"Password updated for '{args.username}'. Existing sessions invalidated.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Shelf operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_reset = sub.add_parser("reset-password", help="Reset a user's password")
    p_reset.add_argument("username")
    p_reset.set_defaults(func=cmd_reset_password)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
