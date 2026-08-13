from pathlib import Path
from pyinfra.api import deploy
from .utils import create_subdirectory


@deploy("Create project directory")
def deploy_project_directory(parent_directory: Path = None,
                             project_directory: str = None,
                             owner: str = None, group: str = None, project_members: list[str] = None):
    #todo: make sure that the project_name is valid and does not contain any special characters or spaces
    #todo: make sure that the owner and group are valid and exist on the system
    
    create_subdirectory(parent_directory=parent_directory,
                        subdirectory_name=project_directory,
                        owner=owner,
                        group=group,
                        mode="2770")

    project_dir = parent_directory / project_directory
    create_subdirectory(parent_directory=project_dir,
                        subdirectory_name='shared',
                        owner=owner,
                        group=group,
                        mode="2770")

    if project_members:
        project_members.append(owner)  # Ensure the owner is included in the project members list
    else:
        project_members = [owner]  # If no project members are provided, create a list with just the owner

    for project_member in project_members:
        create_subdirectory(parent_directory=project_dir, 
                            subdirectory_name=project_member, 
                            owner=project_member, 
                            group=group)
