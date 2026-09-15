"""Initialize container storage and run Astrocade without root privileges."""

import os
import sys
from pathlib import Path
from typing import Final, NoReturn, Protocol, cast

DEFAULT_ID: Final = "1000"
MAX_LINUX_ID: Final = 2**32 - 2
WRITABLE_DIRECTORIES: Final = (Path("/astrocade"),)


class _PosixFunctions(Protocol):
    """POSIX identity operations used by the Linux container."""

    def chown(self, path: Path, uid: int, gid: int, *, follow_symlinks: bool) -> None:
        """Change path ownership."""
        ...

    def getegid(self) -> int:
        """Return the effective group ID."""
        ...

    def geteuid(self) -> int:
        """Return the effective user ID."""
        ...

    def setgid(self, gid: int) -> None:
        """Set the process group ID."""
        ...

    def setgroups(self, groups: list[int]) -> None:
        """Set the supplementary group IDs."""
        ...

    def setuid(self, uid: int) -> None:
        """Set the process user ID."""
        ...


POSIX = cast(_PosixFunctions, os)


def fail(message: str) -> NoReturn:
    """Exit with a container initialization error."""
    print(f"entrypoint: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_linux_id(name: str) -> int:
    """Read and validate a positive numeric Linux user or group ID."""
    raw_value = os.environ.get(name, DEFAULT_ID)
    if not raw_value.isascii() or not raw_value.isdecimal():
        fail(
            f"{name} must be a positive numeric Linux ID between 1 and "
            f"{MAX_LINUX_ID}; got {raw_value!r}"
        )

    value = int(raw_value)
    if not 1 <= value <= MAX_LINUX_ID:
        fail(
            f"{name} must be a positive numeric Linux ID between 1 and "
            f"{MAX_LINUX_ID}; got {raw_value!r}"
        )

    return value


def chown_tree(directory: Path, uid: int, gid: int) -> None:
    """Create and recursively assign one writable directory without following links."""
    if directory.is_symlink():
        fail(f"writable directory must not be a symbolic link: {directory}")

    try:
        directory.mkdir(mode=0o755, parents=True, exist_ok=True)
        POSIX.chown(directory, uid, gid, follow_symlinks=False)

        for root, directories, files in os.walk(directory, followlinks=False):
            for name in [*directories, *files]:
                POSIX.chown(Path(root, name), uid, gid, follow_symlinks=False)
    except OSError as error:
        fail(f"could not initialize writable directory {directory}: {error}")


def main() -> NoReturn:
    """Apply requested IDs when root, then replace this process with the command."""
    if len(sys.argv) < 2:
        fail("no application command was provided")

    uid = parse_linux_id("PUID")
    gid = parse_linux_id("PGID")
    command = sys.argv[1:]

    if POSIX.geteuid() != 0:
        print(
            "entrypoint: container started as non-root; PUID and PGID cannot be "
            f"applied (running as {POSIX.geteuid()}:{POSIX.getegid()})",
            file=sys.stderr,
        )
        os.execvp(command[0], command)

    for directory in WRITABLE_DIRECTORIES:
        chown_tree(directory, uid, gid)

    try:
        POSIX.setgroups([])
        POSIX.setgid(gid)
        POSIX.setuid(uid)
    except OSError as error:
        fail(f"could not switch to UID/GID {uid}:{gid}: {error}")

    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
