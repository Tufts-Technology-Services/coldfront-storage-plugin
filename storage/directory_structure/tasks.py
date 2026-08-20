from pathlib import Path
import logging

from coldfront.core.allocation.models import Allocation
from storage.directory_structure.course import deploy_course_directory
from storage.directory_structure.hpc_projects import deploy_project_directory
from storage.shell_utils import validate_dirname
from storage.directory_structure import PosixDeploymentRunner


logger = logging.getLogger(__name__)



def create_folders(cluster_path: Path, owner: str, group: str, allocation_pk: int, structure_type: str):
    if structure_type == "hpc_project":
        create_project_folders(cluster_path, owner, group, allocation_pk)
    elif structure_type == "hpc_course":
        create_course_folders(cluster_path, owner, group, allocation_pk)
    else:
        logger.error(f"Unknown structure type {structure_type}. Cannot create folders.")
        return

    
def create_project_folders(cluster_path: Path, owner: str, 
                          group: str, allocation_pk: int):
    allocation = Allocation.objects.get(id=allocation_pk)

    members = allocation.allocationuser_set.values_list('user__username', flat=True)

    owner = owner.strip().lower()
    group = group.strip()
    validate_dirname(cluster_path.name)
    runner = PosixDeploymentRunner()
    runner.add_deployment(deploy_project_directory, 
                        parent_directory=cluster_path.parent, 
                        project_directory=cluster_path.name, 
                        project_members=members,
                        owner=owner, 
                        group=group)
    runner.run()


def create_course_folders(cluster_path: Path, owner: str, 
                          group: str, allocation_pk: int):
    allocation = Allocation.objects.get(id=allocation_pk)

    members = allocation.allocationuser_set.values_list('user__username', flat=True)
    owner = owner.strip().lower()
    group = group.strip()
    admin_group = f'{group}admin'
    validate_dirname(cluster_path.name)
    runner = PosixDeploymentRunner()
    runner.add_deployment(deploy_course_directory, 
                        parent_directory=cluster_path.parent, 
                        course_directory=cluster_path.name, 
                        course_members=members,
                        owner=owner, 
                        group=group, 
                        admin_group=admin_group)
    runner.run()
