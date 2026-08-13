from pathlib import Path
from pyinfra.operations import files, python
from pyinfra.api.exceptions import PyinfraError


def create_subdirectory(parent_directory: Path = None,
                        subdirectory_name: str = None,
                        owner: str = None,
                        group: str = None,
                        mode: str = "2770"):
    subdir = parent_directory / subdirectory_name
    try:
        r = files.directory(
            name=f"Create subdirectory {subdirectory_name} in {parent_directory}",
            path=subdir.as_posix(),
            user=owner,
            group=group,
            mode=mode,
        )
        def success_callback():
            if r.stdout:    
              print(f"Got result: {r.stdout}")
    
        python.call(
            name="Execute callback function",
            function=success_callback,
        )
    except PyinfraError as e:
        print(f"Error creating subdirectory {subdirectory_name} in {parent_directory}: {e}")
        def error_callback():
            print(r.stderr)

        python.call(
            name="Execute error callback function",
            function=error_callback,
        )
