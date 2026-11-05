import logging
from pathlib import Path

from pyinfra.api.exceptions import PyinfraError
from pyinfra.context import host
from pyinfra.facts.files import Directory
from pyinfra.operations import files, python

logger = logging.getLogger(__name__)


class ClusterDirectoryNotVisibleError(Exception):
    """A directory the storage system creates can't be seen on the cluster host yet.

    Not a PyinfraError, so create_subdirectory and the runner don't swallow it: it reaches the task,
    which retries. On an autofs mount, a view that was just created can take a while to mount.
    """


def require_existing_directory(path: Path) -> None:
    """Check on the host that `path` is an existing directory, before any operation is added for it.

    Raises ClusterDirectoryNotVisibleError if nothing is there yet, which callers retry, and
    NotADirectoryError if something other than a directory is, which they don't.
    """
    directory_info = host.get_fact(Directory, path=path.as_posix())
    if directory_info is None:
        raise ClusterDirectoryNotVisibleError(f"Directory {path} is not visible on {host.name} yet.")
    if directory_info is False:
        raise NotADirectoryError(f"Path {path} exists on {host.name} but is not a directory.")


def create_subdirectory(
    parent_directory: Path = None,
    subdirectory_name: str = None,
    owner: str = None,
    group: str = None,
    mode: str = "2770",
):
    if parent_directory is None or subdirectory_name is None:
        raise PyinfraError("Parent directory and subdirectory name must be provided.")

    subdir = parent_directory / subdirectory_name
    try:
        r = None
        gstatus, gout = host.run_shell_command(f"getent group {group}")
        if not gstatus:
            raise PyinfraError(f"Group {group} does not exist: {gout}")

        ustatus, uout = host.run_shell_command(f"id -u {owner}")
        if not ustatus:
            raise PyinfraError(f"User {owner} does not exist: {uout}")

        r = files.directory(
            name=f"Create subdirectory {subdirectory_name} in {parent_directory}",
            path=subdir.as_posix(),
            user=owner,
            group=group,
            mode=mode,
        )

        def success_callback(resp=None):
            if resp.stdout:
                logger.info(f"Got result: {resp.stdout}")

        python.call(name="Execute callback function", function=success_callback, resp=r)
    except PyinfraError as e:
        logger.error(f"Error creating subdirectory {subdirectory_name} in {parent_directory}: {e}")

        def error_callback(resp=None):
            if resp and resp.stderr:
                logger.error(resp.stderr)

        if r is not None:
            python.call(name="Execute error callback function", function=error_callback, resp=r)
