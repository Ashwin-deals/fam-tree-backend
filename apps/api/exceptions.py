from rest_framework.exceptions import APIException
from pymongo.errors import PyMongoError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    if isinstance(exc, PyMongoError):
        return Response(
            {
                "error": {
                    "code": "database_unavailable",
                    "message": "The database could not be reached. Check the MongoDB Atlas connection and try again.",
                    "details": {},
                }
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    response = exception_handler(exc, context)
    if response is not None:
        details = response.data
        if isinstance(details, dict):
            message = details.get("detail") or "Please correct the highlighted fields."
        elif isinstance(details, list):
            message = details[0] if details else "Request could not be completed."
        else:
            message = str(details)
        response.data = {
            "error": {
                "code": getattr(exc, "default_code", "request_error"),
                "message": str(message),
                "details": details,
            }
        }
    return response
