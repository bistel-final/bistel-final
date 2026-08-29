"""`python -m app.mes_mock` production entrypoint."""

from __future__ import annotations

import logging
import os
import signal

from app.mes_mock.consumer import MesMockConfig, production_consumer


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    config = MesMockConfig.from_mapping(os.environ)
    service = production_consumer(config)

    def stop(_signum: int, _frame: object) -> None:
        service.stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    service.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
