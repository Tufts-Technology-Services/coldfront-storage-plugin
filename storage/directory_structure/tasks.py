from pathlib import Path
import logging

from coldfront.core.allocation.models import Allocation
from storage.constants import CLUSTER_PATH_ATTRIBUTE_NAME
from storage.directory_structure.course import deploy_course_directory
from storage.directory_structure.hpc_projects import deploy_project_directory
from storage.utils import validate_dirname
from storage.directory_structure import PosixDeploymentRunner
from storage.utils import get_allocation_group


logger = logging.getLogger(__name__)



def create_folders(allocation_pk: int, structure_type: str):
    allocation = Allocation.objects.get(id=allocation_pk)
    owner = allocation.project.pi.username
    group = get_allocation_group(allocation)
    if group is None:
        logger.error(f"No group found for allocation {allocation_pk}")
        raise ValueError(f"No group found for allocation {allocation_pk}")
    aa = allocation.allocationattribute_set.filter(allocation_attribute_type__name=CLUSTER_PATH_ATTRIBUTE_NAME)
    if aa.exists():
        cluster_path = Path(aa.first().value)
    else:
        logger.error(f"No cluster path found for allocation {allocation_pk}")
        raise ValueError(f"No cluster path found for allocation {allocation_pk}")
    members = allocation.allocationuser_set.values_list('user__username', flat=True)

    if structure_type == "hpc_project":
        create_project_folders(cluster_path, owner, group, list(members))
    elif structure_type == "hpc_course":
        create_course_folders(cluster_path, owner, group, list(members))
    else:
        logger.error(f"Unknown structure type {structure_type}. Cannot create folders.")
        raise ValueError(f"Unknown structure type {structure_type}. Cannot create folders.")

    
def create_project_folders(cluster_path: Path, owner: str, 
                          group: str, members: list):
    
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
                          group: str, members: list):
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
