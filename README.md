# envtidy

> Keep your `.env` files honest — catch drift, sync examples, find leaks.

![zero dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

Every team has lived this bug: someone adds `REDIS_URL` to their local `.env`, forgets to update `.env.example`, and the next person to clone the repo burns an hour debugging a crash that was really just a missing variable. Or worse — someone's `staging.env` was never gitignored and ships to GitHub with live credentials inside.

**envtidy** is a tiny CLI that catches both. Pure Python stdlib, zero dependencies, single-purpose.

![envtidy demo](demo.svg)

## Install

```bash
pipx install git+https://github.com/nidhisebastian008/envtidy
# or
pip install git+https://github.com/nidhisebastian008/envtidy
```

## Usage

### `envtidy check` — catch drift

Compares `.env` against `.env.example` (also auto-detects `.sample`, `.template`, `.dist`):

```
$ envtidy check
envtidy check  .env vs .env.example
  missing  REDIS_URL  (in example, not in env)
  extra    DEBUG  (in env, not in example)
  empty    SMTP_HOST  (declared but has no value)

3 issues found (1 missing, 1 extra, 1 empty)
```

Exits `1` when drift is found, `0` when clean — drop it straight into CI or a pre-commit hook:

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: envtidy
      name: envtidy check
      entry: envtidy check
      language: system
      pass_filenames: false
```

### `envtidy sync` — regenerate the example file

Rewrites `.env.example` from your real `.env`, stripping every value but preserving comments, blank lines, and key order:

```
$ envtidy sync
created .env.example (4 keys, values stripped)
```

Use `--dry-run` to preview without writing.

### `envtidy scan` — find files about to leak

Walks a directory tree, finds every env file, and checks each one against git:

```
$ envtidy scan
envtidy scan  ~/code/myapp
  ignored  .env
  exposed  staging.env  (not in .gitignore — one `git add .` from leaking)
  TRACKED  api/.env.production  (committed to git — rotate these secrets)

2 files at risk
```

- **ignored** — safely gitignored ✅
- **exposed** — exists but *not* gitignored; one `git add .` away from a leak
- **TRACKED** — already committed; treat those secrets as compromised and rotate them

## Why not `<other tool>`?

There are heavyweight secret scanners (gitleaks, trufflehog) and schema validators (pydantic-settings) — great tools, different job. envtidy is the *small* tool for the *common* case: keeping `.env` and `.env.example` in lockstep and making sure no env file slips into git. No config file, no rules to write, no dependencies to audit.

## Details

- Understands `export KEY=value`, quoted values, inline `# comments`
- Respects [`NO_COLOR`](https://no-color.org/); colors auto-disable when piped
- Skips `node_modules`, `.venv`, `.git`, build dirs while scanning
- Exit codes: `0` clean · `1` issues found · `2` usage error

## License

MIT
