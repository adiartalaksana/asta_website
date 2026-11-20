"""CMS: client portal accounts.

The CMS is a separate internal area (secret URL + ADMIN_ACCESS_TOKEN, see internal_gate).
It has no portal login, so actions are attributed to "CMS (token)" in the activity log.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.models import SecurityEventLog

from .events import cms_event, log_event
from .forms import PasswordResetForm, UserCreateForm, UserEditForm
from .roles import ROLE_CHOICES, SUPERUSER, assign_role, ensure_roles, role_label, role_of

User = get_user_model()


def _ctx(**extra):
    return {"control_tab": "users", **extra}


def _target(user_id):
    return get_object_or_404(User.objects.prefetch_related("groups"), pk=user_id)


def _is_last_superuser(target):
    return target.is_superuser and target.is_active and not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk).exists()


def _decorate(user):
    user.role = role_of(user)
    user.role_name = role_label(user.role)
    return user


def _edit_initial(target):
    return {
        "username": target.username, "first_name": target.first_name, "last_name": target.last_name,
        "email": target.email, "role": target.role if target.role != SUPERUSER else None, "is_active": target.is_active,
    }


def user_list(request):
    ensure_roles()
    users = User.objects.prefetch_related("groups").order_by("-is_active", "username")
    query = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "")
    status = request.GET.get("status", "")
    if query:
        users = users.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(email__icontains=query)
        )
    if status in ("active", "inactive"):
        users = users.filter(is_active=status == "active")
    rows = [_decorate(user) for user in users]
    if role_filter:
        rows = [user for user in rows if user.role == role_filter]

    everyone = [_decorate(user) for user in User.objects.prefetch_related("groups")]
    from .control_views import _label_events

    events = _label_events(list(SecurityEventLog.objects.select_related("actor").order_by("-created_at")[:15]))
    return render(request, "ledger/users/list.html", _ctx(
        users=rows, query=query, role_filter=role_filter, status=status,
        role_choices=[*ROLE_CHOICES, (SUPERUSER, "Super admin")],
        stats={
            "total": len(everyone),
            "active": sum(1 for user in everyone if user.is_active),
            "staff": sum(1 for user in everyone if user.is_active and user.role not in ("klien", None)),
            "inactive": sum(1 for user in everyone if not user.is_active),
        },
        events=events,
    ))


def user_create(request):
    form = UserCreateForm(request.POST or None, initial={"role": "klien", "is_active": True})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user = User(
                username=data["username"], first_name=data["first_name"], last_name=data["last_name"],
                email=data["email"], is_active=data["is_active"],
            )
            user.set_password(data["password"])
            user.save()
            assign_role(user, data["role"])
            cms_event(request, "user.created", username=user.username, role=data["role"], active=user.is_active)
        if data["generated"]:
            return _password_result(request, user, data["password"], created=True)
        messages.success(request, f"Akun {user.username} berhasil dibuat.")
        return redirect("portal_users:list")
    return render(request, "ledger/users/form.html", _ctx(form=form, mode="create"))


def user_edit(request, user_id):
    target = _decorate(_target(user_id))
    form = UserEditForm(request.POST or None, instance=target, initial=_edit_initial(target), lock_role=target.is_superuser)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        new_role = data.get("role", target.role)
        if not data["is_active"] and _is_last_superuser(target):
            form.add_error("is_active", "Ini satu-satunya super admin aktif dan tidak dapat dinonaktifkan.")
        else:
            changes = {
                field: data[field]
                for field in ("username", "first_name", "last_name", "email", "is_active")
                if getattr(target, field) != data[field]
            }
            with transaction.atomic():
                for field, value in changes.items():
                    setattr(target, field, value)
                target.save()
                if "role" in data and new_role != target.role:
                    assign_role(target, new_role)
                    changes["role"] = new_role
                if changes:
                    cms_event(request, "user.updated", username=target.username, changes=sorted(changes))
            messages.success(request, f"Akun {target.username} berhasil diperbarui.")
            return redirect("portal_users:list")
    return render(request, "ledger/users/form.html", _ctx(
        form=form, mode="edit", target=target, reset_form=PasswordResetForm(user=target),
        protected=_is_last_superuser(target),
    ))


@require_POST
def user_reset_password(request, user_id):
    target = _decorate(_target(user_id))
    form = PasswordResetForm(request.POST, user=target)
    if not form.is_valid():
        edit_form = UserEditForm(instance=target, lock_role=target.is_superuser, initial=_edit_initial(target))
        return render(request, "ledger/users/form.html", _ctx(
            form=edit_form, mode="edit", target=target, reset_form=form, protected=_is_last_superuser(target),
        ), status=400)
    target.set_password(form.cleaned_data["password"])
    target.save(update_fields=["password"])  # also signs the account out of existing portal sessions
    if request.user.is_authenticated and request.user.pk == target.pk:
        update_session_auth_hash(request, target)
    cms_event(request, "user.password_reset", username=target.username, generated=form.cleaned_data["generated"])
    if form.cleaned_data["generated"]:
        return _password_result(request, target, form.cleaned_data["password"], created=False)
    messages.success(request, f"Kata sandi {target.username} berhasil diganti. Sesi lama akun ini telah dikeluarkan.")
    return redirect("portal_users:edit", user_id=target.pk)


@require_POST
def user_toggle_active(request, user_id):
    target = _decorate(_target(user_id))
    if target.is_active and _is_last_superuser(target):
        messages.error(request, "Ini satu-satunya super admin aktif dan tidak dapat dinonaktifkan.")
    else:
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        cms_event(request, "user.activated" if target.is_active else "user.deactivated", username=target.username)
        state = "diaktifkan" if target.is_active else "dinonaktifkan dan tidak dapat masuk lagi"
        messages.success(request, f"Akun {target.username} {state}.")
    return redirect("portal_users:list")


@require_POST
def user_delete(request, user_id):
    target = _decorate(_target(user_id))
    if _is_last_superuser(target):
        messages.error(request, "Ini satu-satunya super admin aktif dan tidak dapat dihapus.")
        return redirect("portal_users:edit", user_id=target.pk)
    username = target.username
    try:
        with transaction.atomic():
            target.delete()
    except ProtectedError:
        messages.error(
            request,
            f"Akun {username} sudah tercatat pada jurnal, rekening koran, atau log audit sehingga tidak dapat dihapus. "
            "Nonaktifkan akun ini agar tidak dapat masuk lagi.",
        )
        return redirect("portal_users:edit", user_id=target.pk)
    cms_event(request, "user.deleted", username=username)
    messages.success(request, f"Akun {username} berhasil dihapus.")
    return redirect("portal_users:list")


@login_required(login_url="portal-login")
def my_password(request):
    """Portal page (not CMS): any signed-in client changes their own password."""
    form = PasswordChangeForm(request.user, request.POST or None)
    for field in form.fields.values():
        field.widget.attrs["autocomplete"] = "new-password"
    form.fields["old_password"].widget.attrs["autocomplete"] = "current-password"
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        log_event(request, "user.password_changed", username=user.username)
        messages.success(request, "Kata sandi Anda berhasil diganti.")
        return redirect("account-password")
    return render(request, "ledger/users/my_password.html", {"form": form, "active_section": "my-password"})


def _password_result(request, user, password, *, created):
    response = render(request, "ledger/users/password_result.html", _ctx(
        target=_decorate(user), password=password, created=created,
    ))
    response["Cache-Control"] = "no-store"  # the one-time password must not be cached
    return response
