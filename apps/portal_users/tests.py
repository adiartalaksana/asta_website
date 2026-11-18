from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.audit.models import SecurityEventLog

from .roles import ACCOUNTANT, ADMINISTRATOR, CLIENT, assign_role, ensure_roles, role_of

User = get_user_model()
STRONG = "Kuat-sekali-2026"


class PortalUserCmsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ensure_roles()
        cls.owner = User.objects.create_superuser("owner", "owner@example.com", STRONG)

    def setUp(self):
        self.client.force_login(self.owner)

    def create(self, **overrides):
        data = {
            "username": "budi", "first_name": "Budi", "last_name": "Santoso", "email": "budi@example.com",
            "role": CLIENT, "is_active": "on", "password_mode": "manual", "password1": STRONG, "password2": STRONG,
            **overrides,
        }
        return self.client.post(reverse("portal_users:create"), data)

    def test_create_client_and_log_in_with_view_only_access(self):
        response = self.create()
        self.assertRedirects(response, reverse("portal_users:list"))
        budi = User.objects.get(username="budi")
        self.assertEqual(role_of(budi), CLIENT)
        self.assertFalse(budi.is_staff)
        self.assertTrue(SecurityEventLog.objects.filter(event_type="user.created", details__username="budi").exists())

        self.client.logout()
        response = self.client.post(reverse("portal-login"), {"username": "budi", "password": STRONG, "next": "/ledger/"})
        self.assertRedirects(response, reverse("ledger:dashboard"))
        self.assertEqual(self.client.get(reverse("ledger:journal-list")).status_code, 200)
        # View-only: cannot open the CMS and gets the branded 403 page.
        response = self.client.get(reverse("portal_users:list"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Anda tidak memiliki izin", status_code=403)
        self.assertNotContains(self.client.get(reverse("ledger:dashboard")), "◎ Pengguna")

    def test_generated_password_is_shown_once_and_works(self):
        response = self.create(username="sari", email="", password_mode="generate", password1="", password2="", role=ACCOUNTANT)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        password = response.context["password"]
        self.assertGreaterEqual(len(password), 14)
        sari = User.objects.get(username="sari")
        self.assertTrue(sari.check_password(password))
        self.assertTrue(sari.is_staff)  # accountants may manage the Chart of Accounts in /admin/
        self.assertTrue(sari.has_perm("ledger.change_journalheader"))
        self.assertFalse(sari.has_perm("auth.change_user"))

    def test_validation(self):
        self.create()
        response = self.create(email="other@example.com")
        self.assertContains(response, "sudah dipakai")
        response = self.create(username="weak", email="weak@example.com", password1="123", password2="123")
        self.assertContains(response, "terlalu singkat", msg_prefix="Django password validators apply")
        self.assertFalse(User.objects.filter(username="weak").exists())

    def test_edit_role_and_deactivate_blocks_login(self):
        self.create()
        budi = User.objects.get(username="budi")
        response = self.client.post(reverse("portal_users:edit", args=[budi.pk]), {
            "username": "budi", "first_name": "Budi", "last_name": "S", "email": "budi@example.com", "role": ADMINISTRATOR, "is_active": "on",
        })
        self.assertRedirects(response, reverse("portal_users:list"))
        budi.refresh_from_db()
        self.assertEqual(role_of(budi), ADMINISTRATOR)
        self.assertTrue(budi.has_perm("auth.change_user"))

        self.client.post(reverse("portal_users:toggle-active", args=[budi.pk]))
        budi.refresh_from_db()
        self.assertFalse(budi.is_active)
        self.client.logout()
        self.client.post(reverse("portal-login"), {"username": "budi", "password": STRONG})
        self.assertEqual(self.client.get(reverse("ledger:dashboard")).status_code, 302)
        self.assertTrue(SecurityEventLog.objects.filter(event_type="auth.login_failed", details__username="budi").exists())

    def test_reset_password_signs_out_existing_sessions(self):
        self.create()
        budi = User.objects.get(username="budi")
        other = self.client_class()
        other.force_login(budi)
        self.assertEqual(other.get(reverse("ledger:dashboard")).status_code, 200)
        response = self.client.post(reverse("portal_users:reset-password", args=[budi.pk]), {"password_mode": "generate"})
        budi.refresh_from_db()
        self.assertTrue(budi.check_password(response.context["password"]))
        self.assertEqual(other.get(reverse("ledger:dashboard")).status_code, 302)

    def test_cannot_lock_yourself_or_remove_last_admin(self):
        response = self.client.post(reverse("portal_users:toggle-active", args=[self.owner.pk]), follow=True)
        self.assertContains(response, "akun Anda sendiri")
        self.client.post(reverse("portal_users:delete", args=[self.owner.pk]))
        self.assertTrue(User.objects.filter(pk=self.owner.pk, is_active=True).exists())

        # An administrator who is the only other manager cannot demote the owner, and vice versa.
        admin = User.objects.create_user("admin2", password=STRONG)
        assign_role(admin, ADMINISTRATOR)
        self.owner.is_active = False
        self.owner.save()
        self.client.force_login(admin)
        response = self.client.post(reverse("portal_users:edit", args=[admin.pk]), {
            "username": "admin2", "role": ADMINISTRATOR, "email": "", "first_name": "", "last_name": "",
        })
        self.assertContains(response, "akun Anda sendiri")
        # Administrators cannot touch a superuser.
        self.assertEqual(self.client.get(reverse("portal_users:edit", args=[self.owner.pk])).status_code, 403)

    def test_delete_unused_account_but_protect_accounts_with_history(self):
        self.create()
        budi = User.objects.get(username="budi")
        response = self.client.post(reverse("portal_users:delete", args=[budi.pk]), follow=True)
        self.assertContains(response, "berhasil dihapus")
        self.assertFalse(User.objects.filter(username="budi").exists())

        self.create(username="lama", email="lama@example.com")
        lama = User.objects.get(username="lama")
        SecurityEventLog.objects.create(actor=lama, event_type="auth.login")
        response = self.client.post(reverse("portal_users:delete", args=[lama.pk]), follow=True)
        self.assertContains(response, "Nonaktifkan akun ini")
        self.assertTrue(User.objects.filter(username="lama").exists())

    def test_change_own_password(self):
        response = self.client.post(reverse("portal_users:my-password"), {
            "old_password": STRONG, "new_password1": "Sandi-baru-2026!", "new_password2": "Sandi-baru-2026!",
        }, follow=True)
        self.assertContains(response, "berhasil diganti")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password("Sandi-baru-2026!"))
        self.assertEqual(self.client.get(reverse("ledger:dashboard")).status_code, 200)  # still signed in

    def test_list_filters(self):
        self.create()
        self.create(username="andi", first_name="Andi", last_name="Wijaya", email="andi@example.com", role=ACCOUNTANT)
        response = self.client.get(reverse("portal_users:list"), {"role": ACCOUNTANT})
        self.assertEqual([u.username for u in response.context["users"]], ["andi"])
        response = self.client.get(reverse("portal_users:list"), {"q": "budi"})
        self.assertEqual([u.username for u in response.context["users"]], ["budi"])
