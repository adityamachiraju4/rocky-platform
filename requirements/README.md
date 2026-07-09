# Rocky Dependency Management

This directory contains the dependency definitions for Project Rocky.

## Files

- `base.txt` — Runtime dependencies required to run the platform.
- `dev.txt` — Development dependencies (includes `base.txt`).
- `test.txt` — Testing dependencies (includes `base.txt`).

## Installation

Development environment:

```bash
pip install -r requirements/dev.txt
```

Production runtime:

```bash
pip install -r requirements/base.txt
```

Testing environment:

```bash
pip install -r requirements/test.txt
```