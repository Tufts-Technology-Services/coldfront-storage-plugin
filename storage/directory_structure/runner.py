from pyinfra.api import Config, Inventory, State
from pyinfra.api.connect import connect_all, disconnect_all
from pyinfra.api.operations import run_ops
from pyinfra.api.deploy import add_deploy
from pyinfra.api.exceptions import PyinfraError

from storage.constants import POSIX_FILESYSTEM_HOST, POSIX_FILESYSTEM_USER, POSIX_FILESYSTEM_SSH_KEY


class PosixDeploymentRunner:
    def __init__(self):
        self.deployments = []

    def add_deployment(self, deployment_function, **kwargs):
        self.deployments.append((deployment_function, kwargs))

    def run(self):
        if POSIX_FILESYSTEM_HOST == "localhost":
            return __pyinfra_run(self.deployments, (["@local"], {}))
        else:
            return __pyinfra_run(self.deployments, ([POSIX_FILESYSTEM_HOST], {}), ssh_user=POSIX_FILESYSTEM_USER, ssh_key=POSIX_FILESYSTEM_SSH_KEY)


def __pyinfra_run(deployments, hosts, ssh_user=None, ssh_key=None) -> list:
    override_data = {}
    if ssh_user:
        override_data['ssh_user'] = ssh_user
    if ssh_key:
        override_data['ssh_key'] = ssh_key

    state = State(inventory=Inventory(hosts, override_data=override_data),
                  config=Config(SUDO=True))
    try:
        connect_all(state)
        for deployment in deployments:
            add_deploy(state, deployment[0], **deployment[1])

        run_ops(state)
        return state
    except PyinfraError as e:
        print(f"Error running deployments: {e}")
        raise e
    finally:
        disconnect_all(state)
