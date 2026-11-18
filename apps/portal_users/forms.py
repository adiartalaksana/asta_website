import secrets

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError

from .roles import ROLE_CHOICES, ROLE_DESCRIPTIONS

User = get_user_model()
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/l/I


def generate_password(user=None, length=14) -> str:
    while True:
        password = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))
        try:
            password_validation.validate_password(password, user)
        except ValidationError:
            continue
        return password


class PasswordChoiceMixin(forms.Form):
    password_mode = forms.ChoiceField(
        label="Kata sandi",
        choices=[("generate", "Buatkan kata sandi acak yang kuat"), ("manual", "Saya tentukan sendiri")],
        initial="generate",
        widget=forms.RadioSelect,
    )
    password1 = forms.CharField(label="Kata sandi baru", required=False, strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Ulangi kata sandi", required=False, strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean_password_fields(self, user):
        data = self.cleaned_data
        if data.get("password_mode") != "manual":
            data["password"] = generate_password(user)
            data["generated"] = True
            return
        password1, password2 = data.get("password1"), data.get("password2")
        if not password1:
            self.add_error("password1", "Isi kata sandi baru.")
            return
        if password1 != password2:
            self.add_error("password2", "Kedua kata sandi tidak sama.")
            return
        try:
            password_validation.validate_password(password1, user)
        except ValidationError as error:
            self.add_error("password1", error)
            return
        data["password"] = password1
        data["generated"] = False


class ProfileFieldsMixin(forms.Form):
    first_name = forms.CharField(label="Nama depan", max_length=150, required=False)
    last_name = forms.CharField(label="Nama belakang", max_length=150, required=False)
    email = forms.EmailField(label="Email", required=False)
    role = forms.ChoiceField(label="Peran", choices=ROLE_CHOICES, widget=forms.RadioSelect)
    is_active = forms.BooleanField(label="Akun aktif (boleh masuk ke portal)", required=False, initial=True)

    role_descriptions = ROLE_DESCRIPTIONS

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if email and User.objects.filter(email__iexact=email).exclude(pk=getattr(self, "instance_pk", None)).exists():
            raise ValidationError("Email ini sudah dipakai akun lain.")
        return email


class UserCreateForm(ProfileFieldsMixin, PasswordChoiceMixin):
    username = forms.CharField(
        label="Nama pengguna",
        max_length=150,
        help_text="Dipakai untuk masuk. Huruf, angka, dan @ . + - _ saja.",
        validators=[User.username_validator],
    )

    field_order = ["username", "first_name", "last_name", "email", "role", "is_active", "password_mode", "password1", "password2"]

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("Nama pengguna ini sudah dipakai.")
        return username

    def clean(self):
        super().clean()
        probe = User(username=self.cleaned_data.get("username", ""), email=self.cleaned_data.get("email", ""),
                     first_name=self.cleaned_data.get("first_name", ""), last_name=self.cleaned_data.get("last_name", ""))
        self.clean_password_fields(probe)
        return self.cleaned_data


class UserEditForm(ProfileFieldsMixin):
    username = forms.CharField(label="Nama pengguna", max_length=150, validators=[User.username_validator])

    field_order = ["username", "first_name", "last_name", "email", "role", "is_active"]

    def __init__(self, *args, instance, lock_role=False, **kwargs):
        self.instance_pk = instance.pk
        super().__init__(*args, **kwargs)
        if lock_role:
            # Superusers keep their status; it can only be changed from the command line.
            del self.fields["role"]

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exclude(pk=self.instance_pk).exists():
            raise ValidationError("Nama pengguna ini sudah dipakai.")
        return username


class PasswordResetForm(PasswordChoiceMixin):
    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        self.clean_password_fields(self.user)
        return self.cleaned_data
