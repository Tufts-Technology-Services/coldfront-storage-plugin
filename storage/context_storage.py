import contextvars

_request_username = contextvars.ContextVar("request_username", default=None)

def get_request_username():
    return _request_username.get()

def set_request_username(username):
    return _request_username.set(username)

def clear_request_username(token):
    _request_username.reset(token)
