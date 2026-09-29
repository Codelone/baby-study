from django.http import Http404
from rest_framework import exceptions
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, exceptions.ValidationError):
        code = "invalid_input"
        message = "提交的内容无效"
        details = response.data
    elif isinstance(exc, (exceptions.NotFound, Http404)):
        code = "not_found"
        message = "未找到所请求的资源"
        details = None
    elif isinstance(exc, exceptions.NotAuthenticated):
        code = "not_authenticated"
        message = "请先登录"
        details = None
    elif isinstance(exc, exceptions.PermissionDenied):
        code = "permission_denied"
        message = "没有权限完成此操作"
        details = None
    else:
        code = getattr(exc, "default_code", "request_error")
        message = str(getattr(exc, "detail", "请求失败"))
        details = None

    response.data = {"error": {"code": code, "message": message}}
    if details is not None:
        response.data["error"]["details"] = details
    return response
