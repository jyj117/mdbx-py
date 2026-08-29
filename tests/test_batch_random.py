from __future__ import annotations

import random

import clibmdbx


def test_batch_primitives(env: clibmdbx.Environment) -> None:
    items = [(f"k{i:04d}".encode(), f"v{i:04d}".encode()) for i in range(1000)]
    with env.write() as txn:
        assert txn.put_many(items) == len(items)
        assert txn.get_many([b"k0000", b"missing", b"k0999"], default=b"-") == [b"v0000", b"-", b"v0999"]
        assert txn.delete_many([b"k0000", b"missing", b"k0999"]) == 2
    with env.read() as txn:
        assert txn.get(b"k0000") is None
        assert txn.get(b"k0500") == b"v0500"


def test_detached_batch_primitives_copy_mutable_inputs(tmp_path) -> None:
    env = clibmdbx.Environment(tmp_path / "detached")
    keys = [bytearray(f"key-{index}".encode()) for index in range(2000)]
    values = [bytearray(f"value-{index}".encode()) for index in range(2000)]
    with env.write() as txn:
        assert txn.put_many(zip(keys, values), detached=True) == len(keys)
        assert txn.get_many(keys, detached=True) == [bytes(value) for value in values]
        keys[0][:] = b"mutated"
        values[0][:] = b"changed"
        assert txn.get(b"key-0") == b"value-0"
        assert txn.delete_many([bytearray(b"key-0"), bytearray(b"missing")], detached=True) == 1
    env.close()


def test_random_operation_sequence_matches_model(env: clibmdbx.Environment) -> None:
    rng = random.Random(0xC11B_D8)
    model: dict[bytes, bytes] = {}
    for _ in range(40):
        commit = rng.randrange(5) != 0
        expected = model.copy()
        txn = env.write()
        for _ in range(100):
            key = rng.randrange(30).to_bytes(2, "little")
            if rng.randrange(3):
                value = rng.randbytes(rng.randrange(0, 80))
                txn.put(key, value)
                expected[key] = value
            else:
                assert txn.delete(key) == (key in expected)
                expected.pop(key, None)
        if commit:
            txn.commit()
            model = expected
        else:
            txn.abort()
        with env.read() as reader:
            keys = [i.to_bytes(2, "little") for i in range(30)]
            assert reader.get_many(keys) == [model.get(key) for key in keys]
