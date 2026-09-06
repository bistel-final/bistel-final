"""Inert Level 3 Compose runner. No app/config import, worker or network activity."""

import signal


def _stop(*_):
    raise SystemExit(0)


def main():
    # Docker init forwards stop signals and reaps children. Do not depend on a
    # shell's `sleep infinity`, nor accidentally inherit backend's ASGI command.
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while True:
        signal.pause()


if __name__ == "__main__":
    main()
