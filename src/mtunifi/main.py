import argparse
import asyncio
import json
import logging
import sys
import time

from pydantic import ValidationError

from mtunifi.config import Settings
from mtunifi.emulator import Emulator
from mtunifi.storage import Store
from mtunifi.telemetry import inform


def main() -> int:
    parser = argparse.ArgumentParser(description="Experimental synthetic UniFi switch emulator")
    parser.add_argument("command", choices=("init", "sample", "run", "diagnose", "health"))
    parser.add_argument("--once", action="store_true", help="Run one inform; nonzero on failure")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        settings = Settings.from_env()
        store = Store(settings.state_dir)
        if args.command in ("diagnose", "health"):
            state = store.load()
            age = time.time() - state.last_inform if state.last_inform else None
            if args.command == "health":
                return (
                    0
                    if (
                        age is not None
                        and 0 <= age <= 660
                        and state.last_error is None
                        and state.controller_url == settings.inform_url
                    )
                    else 1
                )
            print(
                json.dumps(
                    state.public()
                    | {
                        "controller": settings.inform_url,
                        "last_inform_age_seconds": age,
                        "routeros": "not-implemented",
                        "discovered_ports": 0,
                        "synthetic_ports": 8,
                        "wired_clients": 0,
                        "adoption_note": (
                            "Exchanging is a protocol state; verify adoption in controller UI"
                        ),
                    },
                    indent=2,
                )
            )
            return 0
        with store.lock():
            state = store.load_or_create()
            if args.command == "init":
                print(json.dumps(state.public(), indent=2))
            elif args.command == "sample":
                print(json.dumps(inform(settings, state, 0), indent=2))
            else:
                return 0 if asyncio.run(Emulator(settings, store, state).run(args.once)) else 1
    except (OSError, ValidationError, ValueError, RuntimeError) as exc:
        # Validation errors can echo input, including secrets from corrupt state.
        print(
            json.dumps(
                {
                    "event": "startup_error",
                    "category": type(exc).__name__,
                    "hint": "Check configuration, state permissions and exclusive ownership",
                }
            ),
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
