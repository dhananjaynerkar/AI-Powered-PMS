"""Print one scrypt password hash for the ignored local `.env` file."""

from __future__ import annotations

from getpass import getpass

from pms_api.local_auth import create_scrypt_password_hash


def main() -> int:
    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if not password or password != confirmation:
        raise SystemExit("Passwords do not match; no hash was created.")
    print(create_scrypt_password_hash(password))  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
