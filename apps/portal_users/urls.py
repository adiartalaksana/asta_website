from django.urls import path

from . import views

app_name = "portal_users"

urlpatterns = [
    path("", views.user_list, name="list"),
    path("baru/", views.user_create, name="create"),
    path("<int:user_id>/", views.user_edit, name="edit"),
    path("<int:user_id>/kata-sandi/", views.user_reset_password, name="reset-password"),
    path("<int:user_id>/status/", views.user_toggle_active, name="toggle-active"),
    path("<int:user_id>/hapus/", views.user_delete, name="delete"),
    path("kata-sandi-saya/", views.my_password, name="my-password"),
]
