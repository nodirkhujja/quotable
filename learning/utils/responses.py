import json

from django.http import JsonResponse


def json_body(request):
    """Safely parse JSON request body"""
    try:
        # Agar request.body bo'sh bo'lsa yoki noto'g'ri bo'lsa {} qaytaradi
        return json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def error(message, status=400):
    """Standardized error response"""
    return JsonResponse({"ok": False, "error": message}, status=status)


def success(data=None, status=200):
    """Standardized success response"""
    return JsonResponse({"ok": True, **(data or {})}, status=status)
