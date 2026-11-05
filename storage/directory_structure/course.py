from pathlib import Path

from pyinfra.api import deploy

from .utils import create_subdirectory, require_existing_directory, require_subdirectory_arguments


@deploy("Create course directory")
def deploy_course_directory(
    parent_directory: Path = None,
    course_directory_name: str = None,
    course_members: list[str] = None,
    owner: str = None,
    group: str = None,
    admin_group: str = None,
    require_existing: bool = True,
):
    require_subdirectory_arguments(parent_directory, course_directory_name)

    # requires the course directory to exist before proceeding if require_existing is True
    if require_existing:
        require_existing_directory(parent_directory / course_directory_name)

    create_subdirectory(
        parent_directory=parent_directory,
        subdirectory_name=course_directory_name,
        owner=owner,
        group=group,
        mode="2750",
    )

    create_subdirectory(
        parent_directory=parent_directory / course_directory_name,
        subdirectory_name="shared",
        owner=owner,
        group=admin_group,
        mode="2775",
    )

    if course_members:
        course_members.append(owner)  # Ensure the owner is included in the course members list
    else:
        course_members = [owner]  # If no course members are provided, create a list with just the owner
    for course_member in course_members:
        create_subdirectory(
            parent_directory=parent_directory / course_directory_name,
            subdirectory_name=course_member,
            owner=course_member,
            group=admin_group,
            mode="2770",
        )
