"""Create private Compose settings with random passwords. Never overwrite existing settings."""

import os
import secrets
from pathlib import Path


def main():
    directory = Path(__file__).resolve().parent
    contents = (directory / ".env.example").read_text()
    for name in ("MONGO_ROOT_PASSWORD", "MONGO_PASS"):
        contents = contents.replace(f"\n{name}=\n", f"\n{name}={secrets.token_hex(32)}\n")
    try:
        descriptor = os.open(directory / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("Existing .env preserved. Edit it manually if needed.")
        return
    with os.fdopen(descriptor, "w") as output:
        output.write(contents)
    print("Created private .env with independent random MongoDB passwords (values not displayed).")


if __name__ == "__main__":
    main()
