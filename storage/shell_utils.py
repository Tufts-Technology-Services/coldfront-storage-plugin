import subprocess
import re
import shutil


def get_ent(catalog, entry=None):
    cmd = ['getent', catalog]
    if entry:
        cmd.append(entry)
    r = subprocess.run(cmd, capture_output=True, encoding='utf-8')
    return r


def check_user_exists(username):
    """
    Check if a user exists on the system.
    """
    names = get_ent('passwd', entry=username).stdout.splitlines()
    if not names:
        return False
    exact = any(name.split(':')[0] == username for name in names)
    if not exact:
        # check case insensitive match
        case_insensitive = any(name.split(':')[0].lower() == username.lower() for name in names)
        if case_insensitive:
            raise ValueError(f"User {username} exists with different case.")
    return exact


def check_group_exists(groupname):
    """
    Check if a group exists on the system, raises an error if a case insensitive match is found.
    """
    groups = get_ent('group', entry=groupname).stdout.splitlines()
    if not groups:
        return False
    exact = any(group.split(':')[0] == groupname for group in groups)
    if not exact:
        # check case insensitive match
        case_insensitive = any(group.split(':')[0].lower() == groupname.lower() for group in groups)
        if case_insensitive:
            raise ValueError(f"Group {groupname} exists with different case.")
    return exact


def validate_username(name):
    """
    Validate the username. Must be alphanumeric, lowercase, and start with a letter.
    """
    is_valid = name.isalnum() and len(name) < 10 and len(name) > 2 and name.islower() and name[0].isalpha()
    if not is_valid:
        raise ValueError(f'Invalid name for user: {name}')


def validate_groupname(name, max_length=35):
    """
    Validate the group name. a little longer than usernames, also allows underscores and dashes 
    except for the first or last character.
    """
    if name.lower() in ['tts_rsch_beta_cluster_login', 'tts_rsch_hpc_cluster_login']:
        raise ValueError(f'Group name {name.lower()} is reserved and cannot be used.')
    is_valid = re.match("^[a-z0-9_-]+$", name) and len(name) < max_length and name.islower() and name[0].isalpha() and name[-1].isalnum()
    if not is_valid:
        raise ValueError(f'Invalid name for group: {name}')


def validate_dirname(name, max_length=30):
    """
    Validate the directory name. a little longer than usernames, also allows underscores and dashes
    except for the first character."""
    is_valid = re.match("^[a-z0-9_-]+$", name) and len(name) < max_length and name.islower() and name[0].isalpha()
    if not is_valid:
        raise ValueError(f'Invalid name for directory: {name}')
    

def create_subfolder(parent_path, subfolder_name, owner, group, mode=0o2770):
    validate_dirname(subfolder_name)
    validate_username(owner)
    validate_groupname(group)
    if not check_user_exists(owner):
        raise ValueError(f'User {owner} does not exist on the system')
    if parent_path.exists():
        subfolder = parent_path / subfolder_name
        if not subfolder.exists():
            subfolder.mkdir()
        if subfolder.owner() != owner or subfolder.group() != group:
            shutil.chown(subfolder, owner, group)
        if oct(subfolder.stat().st_mode) != oct(mode):
            subfolder.chmod(mode)
        return True
    else:
        raise ValueError(f'Parent path {parent_path} does not exist')