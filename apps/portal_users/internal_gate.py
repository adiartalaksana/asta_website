"""Hide and lock the internal areas (control center CMS and Django admin).

* Both areas live under secret URL prefixes from .env (CONTROL_URL_PATH, ADMIN_URL_PATH).
* Both require the access token from .env (ADMIN_ACCESS_TOKEN) once per browser session.
* The control center (CMS) is separate from the client portal and has no account login:
  the token is the only key. Django admin still asks for its own staff login after the token.
* Nothing on the public site or the client portal links to these prefixes.
"""

import hashlib
import hmac
import time

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.csrf import csrf_protect

from .events import client_ip, log_event

SESSION_KEY = "internal_area_unlock"
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60


def _prefix(path_setting):
    value = (path_setting or "").strip("/")
    return f"/{value}/" if value else None


def token_fingerprint() -> str:
    return hashlib.sha256(settings.ADMIN_ACCESS_TOKEN.encode()).hexdigest()[:24]


def is_unlocked(request) -> bool:
    if not settings.ADMIN_ACCESS_TOKEN:
        return False
    data = request.session.get(SESSION_KEY) or {}
    return data.get("fp") == token_fingerprint() and data.get("until", 0) > time.time()


class InternalAreaGate:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        area = self._area(request.path)
        if area is None:
            return self.get_response(request)
        if not settings.ADMIN_ACCESS_TOKEN:
            raise Http404  # no token configured: the internal areas are closed
        if not is_unlocked(request):
            return csrf_protect(self._unlock)(request, area)
        response = self.get_response(request)
        response["X-Robots-Tag"] = "noindex, nofollow"
        response["Cache-Control"] = "no-store"
        return response

    @staticmethod
    def _area(path):
        for area, setting in (("control", settings.CONTROL_URL_PATH), ("admin", settings.ADMIN_URL_PATH)):
            prefix = _prefix(setting)
            if prefix and (path == prefix.rstrip("/") or path.startswith(prefix)):
                return area
        return None

    def _unlock(self, request, area):
        throttle_key = f"internal-unlock-failures:{client_ip(request)}"
        failures = cache.get(throttle_key, 0)
        error = ""
        status = 200
        if failures >= MAX_FAILURES:
            error = "Terlalu banyak percobaan. Coba lagi dalam 15 menit."
            status = 429
        elif request.method == "POST":
            supplied = request.POST.get("access_token", "")
            if hmac.compare_digest(supplied.encode(), settings.ADMIN_ACCESS_TOKEN.encode()):
                cache.delete(throttle_key)
                request.session.cycle_key()  # new session id once elevated
                request.session[SESSION_KEY] = {
                    "fp": token_fingerprint(),
                    "until": time.time() + settings.ADMIN_UNLOCK_HOURS * 3600,
                }
                log_event(request, "internal.unlocked", area=area)
                return HttpResponseRedirect(request.path)
            cache.set(throttle_key, failures + 1, LOCKOUT_SECONDS)
            log_event(request, "internal.unlock_failed", area=area)
            error = "Token akses tidak valid."
            status = 401
        response = render(request, "internal/unlock.html", {"error": error, "area": area}, status=status)
        response["X-Robots-Tag"] = "noindex, nofollow"
        response["Cache-Control"] = "no-store"
        return response
