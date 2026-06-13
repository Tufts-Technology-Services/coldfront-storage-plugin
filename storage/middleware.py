
from storage.context_storage import set_request_username, clear_request_username


class PassRequestUsernameMiddleware:
    """
    Middleware to set the request username in a ContextVar for use 
    in non-request contexts/threads (e.g. signals)
    This middleware should be placed after authentication middleware to ensure
    that request.user is populated.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            username = request.user.username
            token = set_request_username(username)
            try:
                # Process the view
                response = self.get_response(request)
                return response
            finally:
                # reset the ContextVar to prevent cross-request leaks
                clear_request_username(token)
        else:
            response = self.get_response(request)
        return response
