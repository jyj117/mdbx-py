from __future__ import annotations

import hashlib
import http.server
import json
import pathlib
import subprocess
import sys
import threading
import zipfile

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    # cibuildwheel executes installed-wheel tests from an isolated temporary
    # directory.  These tests deliberately exercise repository release tools,
    # so make their source root explicit instead of relying on the current
    # working directory.
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.check_release_configuration import validate


def test_release_artifact_check_rejects_abi3_and_raw_linux(tmp_path: pathlib.Path) -> None:
    script = pathlib.Path(__file__).parents[1] / "scripts" / "check_release_artifacts.py"
    sdist = tmp_path / "clibmdbx-1.0.1.tar.gz"
    sdist.touch()
    for wheel, expected in [
        ("clibmdbx-1.0.1-cp310-abi3-manylinux_2_17_x86_64.whl", "exact per-CPython ABI"),
        ("clibmdbx-1.0.1-cp310-cp310-linux_x86_64.whl", "unexpected platform tag: linux_x86_64"),
    ]:
        candidate = tmp_path / wheel
        candidate.touch()
        result = subprocess.run(
            [sys.executable, str(script), "1.0.1", str(sdist), str(candidate)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1
        assert expected in result.stderr
        candidate.unlink()


def test_wheel_native_check_rejects_bundled_libmdbx(tmp_path: pathlib.Path) -> None:
    script = pathlib.Path(__file__).parents[1] / "scripts" / "check_wheel_native.py"
    wheel = tmp_path / "clibmdbx-1.0.1-cp310-cp310-manylinux_2_17_x86_64.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("clibmdbx/_core.fake.so", b"not needed for the preflight assertion")
        archive.writestr("clibmdbx/libmdbx.so", b"must never be dynamically bundled")
    result = subprocess.run(
        [sys.executable, str(script), str(wheel)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "unexpected native libraries" in result.stderr


def test_verify_index_release_requires_exact_filenames_and_hashes(tmp_path: pathlib.Path) -> None:
    first = tmp_path / "clibmdbx-1.0.1.tar.gz"
    second = tmp_path / "clibmdbx-1.0.1-cp310-cp310-manylinux_2_17_x86_64.whl"
    first.write_bytes(b"source archive")
    second.write_bytes(b"wheel archive")
    archives = [first, second]
    payload = {
        "urls": [
            {"filename": path.name, "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
            for path in archives
        ]
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        script = pathlib.Path(__file__).parents[1] / "scripts" / "verify_index_release.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--attempts",
                "1",
                "--delay",
                "0",
                "--index",
                f"http://127.0.0.1:{server.server_port}",
                "clibmdbx",
                "1.0.1",
                *(str(path) for path in archives),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "all 2 filenames and SHA-256 digests match" in result.stdout

        payload["urls"][0]["digests"]["sha256"] = "0" * 64
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--attempts",
                "1",
                "--delay",
                "0",
                "--index",
                f"http://127.0.0.1:{server.server_port}",
                "clibmdbx",
                "1.0.1",
                *(str(path) for path in archives),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1
        assert f"changed=['{first.name}']" in result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_release_configuration_rejects_placeholders_and_accepts_real_contacts(tmp_path: pathlib.Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project.urls]\nRepository = "https://example.invalid/repo"\n', encoding="utf-8"
    )
    (tmp_path / "SECURITY.md").write_text("must replace this sentence", encoding="utf-8")
    errors = validate(tmp_path)
    assert len(errors) == 4

    (tmp_path / "pyproject.toml").write_text(
        "\n".join(
            (
                "[project.urls]",
                'Repository = "https://github.com/org/clibmdbx"',
                'Issues = "https://github.com/org/clibmdbx/issues"',
                'Changelog = "https://github.com/org/clibmdbx/blob/main/CHANGELOG.md"',
            )
        ),
        encoding="utf-8",
    )
    (tmp_path / "SECURITY.md").write_text("Email security@example.org privately.", encoding="utf-8")
    assert validate(tmp_path) == []
