from pathlib import Path
from pyinfra.api import deploy
from .utils import create_subdirectory


@deploy("Create personal scratch directory")
def deploy_personal_scratch_directory(parent_directory: Path = None,
                                      username: str = None):
    create_subdirectory(parent_directory=parent_directory,
                        subdirectory_name=username,
                        owner='root',
                        group=f"{username}_g",
                        mode="2770")
