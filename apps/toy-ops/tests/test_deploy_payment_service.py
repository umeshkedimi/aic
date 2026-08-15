from collections.abc import Iterator

import pytest
from aic_database.base import Base
from aic_toy_ops.deploy_payment_service import (
    POOL_SIZE_KEY,
    PRESETS,
    DeploySpec,
    _parse_args,
    record_deployment,
    render_manifest,
)
from sqlalchemy import create_engine


def test_render_manifest_sets_pool_size_version_and_image() -> None:
    manifest = render_manifest(DeploySpec(version="v42", pool_size=3))

    assert "name: payment-service" in manifest
    assert "image: payment-service:dev" in manifest
    assert '- name: DB_POOL_SIZE\n              value: "3"' in manifest
    assert '- name: SERVICE_VERSION\n              value: "v42"' in manifest


def test_parse_args_preset_good() -> None:
    spec = _parse_args(["--preset", "good"])
    assert spec == PRESETS["good"]


def test_parse_args_preset_bad() -> None:
    spec = _parse_args(["--preset", "bad"])
    assert spec == PRESETS["bad"]


def test_parse_args_explicit_pool_size_and_version() -> None:
    spec = _parse_args(["--pool-size", "7", "--version", "v99"])
    assert spec == DeploySpec(version="v99", pool_size=7)


def test_parse_args_requires_version_with_pool_size() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--pool-size", "7"])


@pytest.fixture
def clean_schema(postgres_url: str) -> Iterator[None]:
    engine = create_engine(postgres_url)
    Base.metadata.create_all(engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_record_deployment_writes_real_row_and_computes_diff(
    postgres_url: str, clean_schema: None
) -> None:
    first_id = record_deployment(DeploySpec(version="v41", pool_size=20), database_url=postgres_url)
    second_id = record_deployment(DeploySpec(version="v42", pool_size=3), database_url=postgres_url)

    assert first_id != second_id

    from aic_database.models import Deployment
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    engine = create_engine(postgres_url)
    with Session(engine) as session:
        rows = session.execute(select(Deployment).order_by(Deployment.deployed_at)).scalars().all()

    assert [row.version for row in rows] == ["v41", "v42"]
    assert rows[0].config_diff[POOL_SIZE_KEY] == {"from": None, "to": 20}
    assert rows[1].config_diff[POOL_SIZE_KEY] == {"from": 20, "to": 3}
    engine.dispose()
