from __future__ import annotations

import pathlib

import pytest

import clibmdbx


@pytest.fixture
def env_path(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "mdbx"
    path.mkdir()
    return path


@pytest.fixture
def env(env_path: pathlib.Path):
    instance = clibmdbx.Environment(
        env_path,
        geometry=(0, 1 << 20, 1 << 27, 1 << 20, 2 << 20, -1),
        max_dbs=32,
        max_readers=32,
    )
    try:
        yield instance
    finally:
        if not instance.closed:
            instance.close()
