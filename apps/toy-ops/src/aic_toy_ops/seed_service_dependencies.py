"""One-time (idempotent) seed of the static `ServiceDependency` config
`aic-correlator` reads at startup (design doc §5, T4). Safe to rerun —
`seed_service_dependencies` upserts, never duplicates.
"""

from __future__ import annotations

import os

from aic_common.logging import configure_logging, get_logger
from aic_database.seed import seed_service_dependencies
from aic_database.session import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
    session_scope,
)

logger = get_logger(__name__)


def main() -> int:
    configure_logging()
    url = os.environ["AIC_DATABASE_URL"]
    settings = DatabaseSettings(url=url)
    engine = create_database_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        with session_scope(session_factory) as session:
            seed_service_dependencies(session)
    finally:
        engine.dispose()

    logger.info("toy_ops.service_dependencies_seeded")
    print("service_dependency table seeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
