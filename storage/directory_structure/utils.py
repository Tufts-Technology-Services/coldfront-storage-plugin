import logging
from pathlib import Path
from pyinfra.operations import files, python
from pyinfra.api.exceptions import PyinfraError
from pyinfra import host

logger = logging.getLogger(__name__)


def create_subdirectory(parent_directory: Path = None,
                        subdirectory_name: str = None,
                        owner: str = None,
                        group: str = None,
                        mode: str = "2770"):
    if parent_directory is None or subdirectory_name is None:
        raise PyinfraError("Parent directory and subdirectory name must be provided.")

    subdir = parent_directory / subdirectory_name
    try:
        gstatus, gout = host.run_shell_command(f"getent group {group}")
        if gstatus != 0:
            raise PyinfraError(f"Group {group} does not exist: {gout}")
        
        ustatus, uout = host.run_shell_command(f"id -u {owner}")
        if ustatus != 0:
            raise PyinfraError(f"User {owner} does not exist: {uout}")
        
        r = files.directory(
            name=f"Create subdirectory {subdirectory_name} in {parent_directory}",
            path=subdir.as_posix(),
            user=owner,
            group=group,
            mode=mode,
        )
        def success_callback():
            if r.stdout:    
              logger.info(f"Got result: {r.stdout}")
    
        python.call(
            name="Execute callback function",
            function=success_callback,
        )
    except PyinfraError as e:
        logger.error(f"Error creating subdirectory {subdirectory_name} in {parent_directory}: {e}")
        def error_callback():
            logger.error(r.stderr)

        python.call(
            name="Execute error callback function",
            function=error_callback,
        )
