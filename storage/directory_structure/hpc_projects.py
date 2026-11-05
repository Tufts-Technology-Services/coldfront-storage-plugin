from pathlib import Path

from pyinfra.api.deploy import deploy

from .utils import create_subdirectory, require_existing_directory, require_subdirectory_arguments


@deploy("Create project directory")
def deploy_project_directory(
    parent_directory: Path = None,
    project_directory_name: str = None,
    owner: str = None,
    group: str = None,
    project_members: list[str] = None,
    require_existing: bool = True,
):
    require_subdirectory_arguments(parent_directory, project_directory_name)
    project_dir = parent_directory / project_directory_name

    # requires the project directory to exist before proceeding if require_existing is True
    if require_existing:
        require_existing_directory(project_dir)

    create_subdirectory(
        parent_directory=parent_directory,
        subdirectory_name=project_directory_name,
        owner=owner,
        group=group,
        mode="2770",
    )

    create_subdirectory(parent_directory=project_dir, subdirectory_name="shared", owner=owner, group=group, mode="2770")

    if project_members:
        project_members.append(owner)  # Ensure the owner is included in the project members list
    else:
        project_members = [owner]  # If no project members are provided, create a list with just the owner

    for project_member in project_members:
        create_subdirectory(
            parent_directory=project_dir, subdirectory_name=project_member, owner=project_member, group=group
        )
