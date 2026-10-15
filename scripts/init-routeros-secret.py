"""Create a local Compose password file interactively; never print its contents."""

import getpass
import os
from pathlib import Path


def main():
    directory = Path(__file__).resolve().parents[1] / ".secrets"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise SystemExit("Set .secrets directory permissions to 0700 before storing a password")
    path = directory / "routeros-password"
    if path.exists():
        raise SystemExit("Existing password file preserved; rotate it explicitly if required")
    password = getpass.getpass("RouterOS read-only API password: ")
    if not password or len(password.encode()) > 4096 or any(c in password for c in "\r\n\0"):
        raise SystemExit("Expected a nonempty single-line password, at most 4096 bytes")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(password + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            # Compose file secrets are bind mounts: make them readable by container UID 10001.
            # The parent directory stays 0700, preventing other host users from traversing it.
            os.fchmod(handle.fileno(), 0o444)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    print("Created .secrets/routeros-password; contents are never copied into the image or Git")


if __name__ == "__main__":
    main()
