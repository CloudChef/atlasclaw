# Python dependency upgrades

The supported release runtime is CPython **3.14.10** (standard GIL build). Development
uses `.python-version`; create a fresh venv rather than reusing an older interpreter's
site-packages. The existing virtual environment should be retained until validation passes.

`requirements.txt` declares the runtime dependencies installed by the Core Docker
images. `requirements-dev.txt` includes those requirements and adds test dependencies.
Install with `python -m pip install -r requirements-dev.txt`, then run
`python -m pip check`. Model initialization and tool argument binding are
covered by the test suite.

When updating dependencies, review the resolved versions and validate the intended
Linux target on Python 3.14.10 before release.
A successful cross-platform resolution does not prove that native wheels load on Linux.

Integration packaging resolves the component requirements and bundles the resulting
wheels for offline installation. Build tools are isolated from runtime dependencies.

## Compatibility exception in the November 2026 snapshot

Starlette is constrained to **1.8.0, below 1.9**. With the updated dependency set,
1.9.0 and 1.10.0 reproducibly fail the existing channel deletion test with a SQLite lock.
1.8.0 passes the same test with SQLAlchemy 2.1.8. This is a tested compatibility bound,
not a claim that every deployment of upstream Starlette is affected. Re-evaluate the
bound against the channel API and database tests before lifting it.

Pydantic AI and its companion distributions follow their exact upstream constraints.
In particular, the selected framework constrains Google GenAI below 3; do not override
that constraint to force an independently newer SDK. Model HTTP tracing uses httpx2;
other application and integration HTTP clients may continue using httpx.
