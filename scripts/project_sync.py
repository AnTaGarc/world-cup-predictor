from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import subprocess
import sys


DURABLE_PATHS = (
    "data/worldcup.sqlite",
    "data/models",
    "data/fixtures",
    "data/evidence/reviewed-json",
    "data/precomputed",
)

FORBIDDEN_PREFIXES = (
    "data/cache/",
    "output/",
    ".codex-remote-attachments/",
    ".pytest_cache/",
    "__pycache__/",
)

FORBIDDEN_SUFFIXES = (
    ".log",
    ".sqlite-wal",
    ".sqlite-shm",
    ".sqlite-journal",
    ".bak",
    ".tmp",
)


class SyncError(RuntimeError):
    pass


def _normal_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def is_forbidden_path(path: str) -> bool:
    normalized = _normal_path(path)
    return normalized.startswith(FORBIDDEN_PREFIXES) or normalized.endswith(
        FORBIDDEN_SUFFIXES
    )


def checkpoint_and_validate_sqlite(db_path: Path) -> None:
    if not db_path.is_file():
        raise SyncError(f"La base SQLite no existe: {db_path}")
    con = None
    try:
        con = sqlite3.connect(db_path, timeout=5)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        result = str(con.execute("PRAGMA integrity_check").fetchone()[0])
    except sqlite3.Error as exc:
        raise SyncError(
            "No se pudo cerrar y validar SQLite. Cierra la aplicación y vuelve a "
            f"intentarlo: {exc}"
        ) from exc
    finally:
        if con is not None:
            con.close()
    if result.casefold() != "ok":
        raise SyncError(f"SQLite no supera integrity_check: {result}")


def _run(
    root: Path, args: tuple[str, ...], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args), cwd=root, capture_output=True, text=True, encoding="utf-8",
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SyncError(f"Falló {' '.join(args)}: {detail}")
    return result


def run_git(root: Path, *args: str) -> str:
    return _run(root, ("git", *args)).stdout.strip()


def _nul_paths(output: str) -> list[str]:
    return [_normal_path(value) for value in output.split("\0") if value]


def validate_repository(root: Path) -> None:
    if run_git(root, "rev-parse", "--is-inside-work-tree") != "true":
        raise SyncError(f"No es un repositorio Git: {root}")


def validate_durable_paths(root: Path) -> None:
    validate_repository(root)
    for relative in DURABLE_PATHS:
        path = root / relative
        if not path.exists():
            raise SyncError(f"Falta la ruta persistente obligatoria: {relative}")
        ignored = _run(
            root, ("git", "check-ignore", "-q", "--", relative), check=False
        )
        if ignored.returncode == 0:
            raise SyncError(f"La ruta persistente está ignorada por Git: {relative}")


def staged_paths(root: Path) -> list[str]:
    return _nul_paths(run_git(root, "diff", "--cached", "--name-only", "-z"))


def stage_and_validate(root: Path) -> list[str]:
    validate_durable_paths(root)
    run_git(root, "add", "-A")
    staged = staged_paths(root)
    forbidden = [path for path in staged if is_forbidden_path(path)]
    if forbidden:
        raise SyncError(
            "Hay rutas prohibidas preparadas para commit: " + ", ".join(forbidden)
        )
    omitted = _nul_paths(run_git(root, "diff", "--name-only", "-z"))
    omitted.extend(
        _nul_paths(run_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    )
    if omitted:
        raise SyncError(
            "Quedan archivos versionables fuera del commit: " + ", ".join(omitted)
        )
    return staged


def _validate_sync_target(root: Path, remote: str, branch: str) -> None:
    validate_repository(root)
    current = run_git(root, "branch", "--show-current")
    if current != branch:
        raise SyncError(
            f"La sincronización solo está permitida en {branch}; rama actual: {current}"
        )
    _run(root, ("git", "remote", "get-url", remote))


def _versionable_status(root: Path) -> list[str]:
    output = run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    return [line for line in output.splitlines() if line]


def pull_project(
    root: Path,
    remote: str = "origin",
    branch: str = "main",
    *,
    what_if: bool = False,
) -> str:
    _validate_sync_target(root, remote, branch)
    local = _versionable_status(root)
    if local:
        raise SyncError(
            "Hay cambios locales versionables. Ejecuta primero "
            "scripts/push_project.ps1. Cambios: " + " | ".join(local)
        )
    if what_if:
        return run_git(root, "rev-parse", "HEAD")
    run_git(root, "fetch", remote)
    run_git(root, "merge", "--ff-only", f"{remote}/{branch}")
    database = root / "data/worldcup.sqlite"
    if database.exists():
        checkpoint_and_validate_sqlite(database)
    return run_git(root, "rev-parse", "HEAD")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sincronización segura del proyecto y sus datos deportivos."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    pull = subparsers.add_parser("pull", help="Actualiza main solo por fast-forward.")
    pull.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    pull.add_argument("--what-if", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "pull":
            head = pull_project(args.root, what_if=args.what_if)
            mode = "Comprobación" if args.what_if else "Pull"
            print(f"{mode} correcto. HEAD: {head}")
            return 0
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
