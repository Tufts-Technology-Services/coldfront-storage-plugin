from pathlib import Path
from pyinfra.api import deploy
from .utils import create_subdirectory


@deploy("Create course directory")
def deploy_course_directory(parent_directory: Path = None,
                            course_directory: str = None,
                            course_members: list[str] = None,
                            owner: str = None,
                            group: str = None,
                            admin_group: str = None):
   
    create_subdirectory(parent_directory=parent_directory,
                        subdirectory_name=course_directory,
                        owner=owner,
                        group=group,
                        mode="2750")

    create_subdirectory(parent_directory=parent_directory / course_directory,
                        subdirectory_name='shared',
                        owner=owner,
                        group=admin_group,
                        mode="2775")

    if course_members:
        course_members.append(owner)  # Ensure the owner is included in the course members list
    else:
        course_members = [owner]  # If no course members are provided, create a list with just the owner
    for course_member in course_members:
        create_subdirectory(parent_directory=parent_directory / course_directory,
                            subdirectory_name=course_member,
                            owner=course_member,
                            group=admin_group,
                            mode="2770")
