"""envtidy — keep your .env files honest.

Zero-dependency CLI that catches .env drift, generates sanitized
.env.example files, and finds env files at risk of being committed.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from . import __version__

# ---------------------------------------------------------------------------
# Terminal colors (respects NO_COLOR and non-tty output)
# ---------------------------------------------------------------------------


def _use_color() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR") is not None:
        return True
    return sys.stdout.isatty()


class C:
    enabled = _use_color()

    @classmethod
    def _wrap(cls, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if cls.enabled else text

    @classmethod
    def red(cls, t: str) -> str:
        return cls._wrap("31", t)

    @classmethod
    def green(cls, t: str) -> str:
        return cls._wrap("32", t)

    @classmethod
    def yellow(cls, t: str) -> str:
        return cls._wrap("33", t)

    @classmethod
    def cyan(cls, t: str) -> str:
        return cls._wrap("36", t)

    @classmethod
    def dim(cls, t: str) -> str:
        return cls._wrap("2", t)

    @classmethod
    def bold(cls, t: str) -> str:
        return cls._wrap("1", t)


# ---------------------------------------------------------------------------
# Dotenv parsing
# ---------------------------------------------------------------------------

# KEY=VALUE with optional `export ` prefix; permissive on key charset
_LINE_RE = re.compile(
    r"""^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(?P<value>.*)$"""
)


def parse_env(path: Path) -> dict[str, str]:
    """Parse a dotenv file into an ordered {key: raw_value} dict."""
    entries: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(raw)
        if not m:
            continue
        value = m.group("value").strip()
        # Strip surrounding quotes and trailing unquoted comments
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        entries[m.group("key")] = value
    return entries


# ---------------------------------------------------------------------------
# check — compare .env against .env.example
# ---------------------------------------------------------------------------

EXAMPLE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def find_example(env_path: Path) -> Path | None:
    for suffix in EXAMPLE_SUFFIXES:
        candidate = env_path.with_name(env_path.name + suffix)
        if candidate.is_file():
            return candidate
    return None


def cmd_check(args: argparse.Namespace) -> int:
    env_path = Path(args.env)
    if not env_path.is_file():
        print(f"{C.red('error:')} {env_path} not found", file=sys.stderr)
        return 2

    example_path = Path(args.example) if args.example else find_example(env_path)
    if example_path is None or not example_path.is_file():
        print(
            f"{C.red('error:')} no example file found next to {env_path} "
            f"(tried {', '.join(env_path.name + s for s in EXAMPLE_SUFFIXES)})",
            file=sys.stderr,
        )
        return 2

    env = parse_env(env_path)
    example = parse_env(example_path)

    missing = [k for k in example if k not in env]
    extra = [k for k in env if k not in example]
    empty = [k for k, v in env.items() if v == "" and k in example]

    print(C.bold(f"envtidy check  {C.dim(f'{env_path} vs {example_path}')}"))
    issues = 0
    for key in missing:
        issues += 1
        print(f"  {C.red('missing')}  {key}  {C.dim('(in example, not in env)')}")
    for key in extra:
        issues += 1
        print(f"  {C.yellow('extra')}    {key}  {C.dim('(in env, not in example)')}")
    for key in empty:
        issues += 1
        print(f"  {C.yellow('empty')}    {key}  {C.dim('(declared but has no value)')}")

    if issues == 0:
        print(f"  {C.green('ok')}       {len(env)} keys in sync")
        return 0
    print(
        f"\n{C.bold(str(issues))} issue{'s' if issues != 1 else ''} found "
        f"({len(missing)} missing, {len(extra)} extra, {len(empty)} empty)"
    )
    if extra and not args.no_hint:
        print(C.dim("hint: run `envtidy sync` to update the example file"))
    return 1


# ---------------------------------------------------------------------------
# sync — generate/update a sanitized .env.example from .env
# ---------------------------------------------------------------------------


def sanitize_lines(env_path: Path) -> list[str]:
    """Rewrite dotenv content with values stripped, preserving structure."""
    out: list[str] = []
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            out.append(raw)
            continue
        m = _LINE_RE.match(raw)
        if m:
            prefix = "export " if raw.lstrip().startswith("export ") else ""
            out.append(f"{prefix}{m.group('key')}=")
        else:
            out.append(raw)
    return out


def cmd_sync(args: argparse.Namespace) -> int:
    env_path = Path(args.env)
    if not env_path.is_file():
        print(f"{C.red('error:')} {env_path} not found", file=sys.stderr)
        return 2

    example_path = (
        Path(args.example)
        if args.example
        else (find_example(env_path) or env_path.with_name(env_path.name + ".example"))
    )
    content = "\n".join(sanitize_lines(env_path)) + "\n"

    if args.dry_run:
        sys.stdout.write(content)
        return 0

    existed = example_path.exists()
    example_path.write_text(content, encoding="utf-8")
    verb = "updated" if existed else "created"
    n_keys = len(parse_env(env_path))
    print(f"{C.green(verb)} {example_path} ({n_keys} keys, values stripped)")
    return 0


# ---------------------------------------------------------------------------
# scan — find env files at risk of being committed
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".tox", ".mypy_cache", "dist", "build", ".next", "target",
}

_ENV_NAME_RE = re.compile(r"^\.env(\..+)?$|^.+\.env$")


def is_env_file(name: str) -> bool:
    if any(name.endswith(s) for s in EXAMPLE_SUFFIXES):
        return False
    return bool(_ENV_NAME_RE.match(name))


def _git(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *argv],
        capture_output=True,
        text=True,
        check=False,
    )


def env_files_in_history(root: Path) -> set[str]:
    """Paths (relative to scan root) of env files ever added to git history."""
    prefix = _git(root, "rev-parse", "--show-prefix").stdout.strip()
    log = _git(
        root, "log", "--all", "--diff-filter=A", "--name-only", "--pretty=format:"
    )
    if log.returncode != 0:
        return set()
    hits: set[str] = set()
    for line in log.stdout.splitlines():
        line = line.strip()
        if not line or not is_env_file(os.path.basename(line)):
            continue
        # git log paths are relative to the repo top level; keep only those
        # under the scan root and re-relativize them to it
        if prefix:
            if not line.startswith(prefix):
                continue
            line = line[len(prefix):]
        hits.add(line)
    return hits


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"{C.red('error:')} {root} is not a directory", file=sys.stderr)
        return 2

    env_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if is_env_file(name):
                env_files.append(Path(dirpath) / name)

    print(C.bold(f"envtidy scan  {C.dim(str(root))}"))
    in_git = _git(root, "rev-parse", "--is-inside-work-tree").returncode == 0
    history = env_files_in_history(root) if in_git else set()

    if not env_files and not history:
        print(f"  {C.green('ok')}       no env files found")
        return 0

    issues = 0
    seen: set[str] = set()
    for path in sorted(env_files):
        rel = str(path.relative_to(root))
        seen.add(rel)
        if not in_git:
            print(f"  {C.cyan('found')}    {rel}  {C.dim('(not a git repo)')}")
            continue
        tracked = _git(root, "ls-files", "--error-unmatch", rel).returncode == 0
        if tracked:
            issues += 1
            print(f"  {C.red('TRACKED')}  {rel}  {C.dim('(committed to git — rotate these secrets)')}")
            continue
        if rel in history:
            issues += 1
            print(f"  {C.red('HISTORY')}  {rel}  {C.dim('(untracked now, but still in git history — rotate + scrub)')}")
            continue
        ignored = _git(root, "check-ignore", "-q", rel).returncode == 0
        if ignored:
            print(f"  {C.green('ignored')}  {rel}")
        else:
            issues += 1
            print(f"  {C.yellow('exposed')}  {rel}  {C.dim('(not in .gitignore — one `git add .` from leaking)')}")

    # env files deleted from the working tree but still recoverable from history
    for rel in sorted(history - seen):
        if _git(root, "ls-files", "--error-unmatch", rel).returncode == 0:
            continue  # currently tracked under a path outside the walk (skipped dir)
        issues += 1
        print(f"  {C.red('HISTORY')}  {rel}  {C.dim('(deleted, but still in git history — rotate + scrub)')}")

    if issues:
        print(f"\n{C.bold(str(issues))} file{'s' if issues != 1 else ''} at risk")
        if any(rel in history for rel in seen) or history - seen:
            print(C.dim("hint: scrub history with `git filter-repo --sensitive-data-removal --invert-paths --path <file>`"))
        return 1
    print(f"\n{C.green('all clear')} — every env file is gitignored and absent from git history")
    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="envtidy",
        description="Keep your .env files honest: catch drift, sync examples, find leaks.",
    )
    parser.add_argument("--version", action="version", version=f"envtidy {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="compare .env against .env.example")
    p_check.add_argument("env", nargs="?", default=".env", help="env file (default: .env)")
    p_check.add_argument("--example", help="example file (default: auto-detect)")
    p_check.add_argument("--no-hint", action="store_true", help=argparse.SUPPRESS)
    p_check.set_defaults(func=cmd_check)

    p_sync = sub.add_parser("sync", help="generate .env.example from .env (values stripped)")
    p_sync.add_argument("env", nargs="?", default=".env", help="env file (default: .env)")
    p_sync.add_argument("--example", help="output file (default: <env>.example)")
    p_sync.add_argument("--dry-run", action="store_true", help="print result instead of writing")
    p_sync.set_defaults(func=cmd_sync)

    p_scan = sub.add_parser("scan", help="find env files at risk of being committed")
    p_scan.add_argument("dir", nargs="?", default=".", help="directory to scan (default: .)")
    p_scan.set_defaults(func=cmd_scan)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
