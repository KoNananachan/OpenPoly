# user_sections — trust boundary

Drop your own section impls here. The registry scans this directory at backend
startup and registers any class satisfying the Section Protocol (see
`openpoly/sections/_base.py`).

## Layout

Flat or nested; the registry walks recursively. Examples:

```
openpoly/user_sections/
├── my_analyzer.py
└── trader/
    └── my_strategy.py
```

Each file may define one or more classes with the required class attributes
(`SECTION_TYPE`, `SECTION_VERSION`, `REQUIRES`, `Config`, `run`). Optionally
declare a `CONTRACT_TEST` `@staticmethod` on the class — the registry will
invoke it and reject the impl if it raises.

## Trust boundary disclaimer

openPoly is a **single-user local system**. Files here execute under the same
privileges as the backend process. We do **not** sandbox user scripts. Only put
code here that you wrote or audited yourself.

The registry performs minimal hygiene checks (required attrs, capability list
validation, contract test invocation) but does not constrain what your `run()`
does — including filesystem, network, or subprocess access.

## Files in this directory are gitignored

`__init__.py` and `README.md` are tracked; everything else is ignored so your
private strategies stay local. See repo-root `.gitignore`.
