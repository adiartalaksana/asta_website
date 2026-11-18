from urllib.parse import urlencode

from django.contrib import admin
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import include, path, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import RedirectView, TemplateView


def healthcheck(_request):
    return JsonResponse({"service": "ledger-platform", "status": "ok"})


def safe_next_url(request, next_url):
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return reverse("ledger:dashboard")


def login_modal_url(next_url=None):
    query = f"?{urlencode({'next': next_url})}" if next_url else ""
    return f"{reverse('home')}{query}#portal-login"


def portal_login(request):
    next_url = request.POST.get("next") if request.method == "POST" else request.GET.get("next")
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect(safe_next_url(request, next_url))
        messages.error(request, "Nama pengguna atau kata sandi tidak sesuai.")
        return redirect(login_modal_url(next_url))

    if request.user.is_authenticated:
        return redirect(safe_next_url(request, next_url))
    # Keep ?next= so the user lands on the page they originally requested after login.
    return redirect(login_modal_url(next_url))


@require_POST
def portal_logout(request):
    logout(request)
    return redirect("home")


urlpatterns = [
    path("", TemplateView.as_view(template_name="landing/index.html"), name="home"),
    path("login/", portal_login, name="portal-login"),
    path("logout/", portal_logout, name="portal-logout"),
    path("admin/", admin.site.urls),
    path("ledger/users/", include("apps.portal_users.urls")),
    # Old Indonesian links (bookmarks) keep working.
    path("ledger/pengguna/", RedirectView.as_view(pattern_name="portal_users:list", permanent=True)),
    path("ledger/pengguna/kata-sandi-saya/", RedirectView.as_view(pattern_name="portal_users:my-password", permanent=True)),
    path("ledger/", include("apps.ledger.urls")),
    path("health/", healthcheck, name="healthcheck"),
]
