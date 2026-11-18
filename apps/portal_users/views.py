from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.models import SecurityEventLog

from .events import EVENT_LABELS, log_event
from .forms import PasswordResetForm, UserCreateForm, UserEditForm
from .roles import (
    ADMINISTRATOR,
    ROLE_CHOICES,
    ROLE_GROUPS,
    SUPERUSER,
    assign_role,
    can_manage_users,
    ensure_roles,
    is_manager_account,
    role_label,
    role_of,
)

User = get_user_model()
SECTION = "users"


def manager_required(view):
    @login_required(login_url="portal-login")
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not can_manage_users(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def _target(request, user_id):
    target = get_object_or_404(User.objects.prefetch_related("groups"), pk=user_id)
    if target.is_superuser and not request.user.is_superuser:
        raise PermissionDenied  # only a superuser may manage another superuser
    return target


def _other_active_managers(target):
    managers = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(groups__name=ROLE_GROUPS[ADMINISTRATOR])
    )
    return managers.exclude(pk=target.pk).distinct().exists()


def _decorate(user):
    user.role = role_of(user)
    user.role_name = role_label(user.role)
    return user


@manager_required
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
    events = list(SecurityEventLog.objects.select_related("actor").order_by("-created_at")[:15])
    for event in events:
        event.label = EVENT_LABELS.get(event.event_type, event.event_type)
    return render(request, "ledger/users/list.html", {
        "users": rows,
        "query": query,
        "role_filter": role_filter,
        "status": status,
        "role_choices": [*ROLE_CHOICES, (SUPERUSER, "Super admin")],
        "stats": {
            "total": len(everyone),
            "active": sum(1 for user in everyone if user.is_active),
            "managers": sum(1 for user in everyone if user.is_active and user.role in (ADMINISTRATOR, SUPERUSER)),
            "inactive": sum(1 for user in everyone if not user.is_active),
        },
        "events": events,
        "active_section": SECTION,
    })


@manager_required
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
            log_event(request, "user.created", username=user.username, role=data["role"], active=user.is_active)
        if data["generated"]:
            return _password_result(request, user, data["password"], created=True)
        messages.success(request, f"Akun {user.username} berhasil dibuat.")
        return redirect("portal_users:list")
    return render(request, "ledger/users/form.html", {"form": form, "mode": "create", "active_section": SECTION})


@manager_required
def user_edit(request, user_id):
    target = _decorate(_target(request, user_id))
    initial = {
        "username": target.username, "first_name": target.first_name, "last_name": target.last_name,
        "email": target.email, "role": target.role if target.role != SUPERUSER else None, "is_active": target.is_active,
    }
    form = UserEditForm(request.POST or None, instance=target, initial=initial, lock_role=target.is_superuser)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        new_role = data.get("role", target.role)
        is_self = target.pk == request.user.pk
        losing_manager = target.role in (ADMINISTRATOR, SUPERUSER) and target.is_active and (
            not data["is_active"] or (new_role not in (ADMINISTRATOR, SUPERUSER))
        )
        if is_self and not data["is_active"]:
            form.add_error("is_active", "Anda tidak dapat menonaktifkan akun Anda sendiri.")
        elif is_self and new_role != target.role:
            form.add_error("role", "Anda tidak dapat mengubah peran akun Anda sendiri.")
        elif losing_manager and not _other_active_managers(target):
            form.add_error(None, "Harus ada minimal satu administrator aktif. Tambahkan administrator lain terlebih dahulu.")
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
                    log_event(request, "user.updated", username=target.username, changes=sorted(changes))
            messages.success(request, f"Akun {target.username} berhasil diperbarui.")
            return redirect("portal_users:list")
    return render(request, "ledger/users/form.html", {
        "form": form, "mode": "edit", "target": target, "reset_form": PasswordResetForm(user=target),
        "is_self": target.pk == request.user.pk, "active_section": SECTION,
    })


@manager_required
@require_POST
def user_reset_password(request, user_id):
    target = _decorate(_target(request, user_id))
    form = PasswordResetForm(request.POST, user=target)
    if not form.is_valid():
        edit_form = UserEditForm(instance=target, lock_role=target.is_superuser, initial={
            "username": target.username, "first_name": target.first_name, "last_name": target.last_name,
            "email": target.email, "role": target.role, "is_active": target.is_active,
        })
        return render(request, "ledger/users/form.html", {
            "form": edit_form, "mode": "edit", "target": target, "reset_form": form,
            "is_self": target.pk == request.user.pk, "active_section": SECTION,
        }, status=400)
    target.set_password(form.cleaned_data["password"])
    target.save(update_fields=["password"])  # also signs the user out of existing sessions
    if target.pk == request.user.pk:
        update_session_auth_hash(request, target)
    log_event(request, "user.password_reset", username=target.username, generated=form.cleaned_data["generated"])
    if form.cleaned_data["generated"]:
        return _password_result(request, target, form.cleaned_data["password"], created=False)
    messages.success(request, f"Kata sandi {target.username} berhasil diganti. Sesi lama akun ini telah dikeluarkan.")
    return redirect("portal_users:edit", user_id=target.pk)


@manager_required
@require_POST
def user_toggle_active(request, user_id):
    target = _decorate(_target(request, user_id))
    if target.pk == request.user.pk:
        messages.error(request, "Anda tidak dapat menonaktifkan akun Anda sendiri.")
    elif target.is_active and target.role in (ADMINISTRATOR, SUPERUSER) and not _other_active_managers(target):
        messages.error(request, "Harus ada minimal satu administrator aktif.")
    else:
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        log_event(request, "user.activated" if target.is_active else "user.deactivated", username=target.username)
        state = "diaktifkan" if target.is_active else "dinonaktifkan dan tidak dapat masuk lagi"
        messages.success(request, f"Akun {target.username} {state}.")
    return redirect("portal_users:list")


@manager_required
@require_POST
def user_delete(request, user_id):
    target = _decorate(_target(request, user_id))
    if target.pk == request.user.pk:
        messages.error(request, "Anda tidak dapat menghapus akun Anda sendiri.")
        return redirect("portal_users:edit", user_id=target.pk)
    if target.role in (ADMINISTRATOR, SUPERUSER) and not _other_active_managers(target):
        messages.error(request, "Harus ada minimal satu administrator aktif.")
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
    log_event(request, "user.deleted", username=username)
    messages.success(request, f"Akun {username} berhasil dihapus.")
    return redirect("portal_users:list")


@login_required(login_url="portal-login")
def my_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    for field in form.fields.values():
        field.widget.attrs["autocomplete"] = "new-password"
    form.fields["old_password"].widget.attrs["autocomplete"] = "current-password"
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        log_event(request, "user.password_changed", username=user.username)
        messages.success(request, "Kata sandi Anda berhasil diganti.")
        return redirect("portal_users:my-password")
    return render(request, "ledger/users/my_password.html", {"form": form, "active_section": "my-password"})


def _password_result(request, user, password, *, created):
    response = render(request, "ledger/users/password_result.html", {
        "target": _decorate(user), "password": password, "created": created, "active_section": SECTION,
    })
    response["Cache-Control"] = "no-store"  # the one-time password must not be cached
    return response
