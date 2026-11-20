import json
import time
import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.ai_engine import gemini
from apps.ai_engine.agents import MAPPER, READER, SUMMARIZER, compose_prompt, load_configs
from apps.ai_engine.models import AgentProfile
from apps.audit.models import SecurityEventLog

from .internal_gate import SESSION_KEY, token_fingerprint
from .roles import ACCOUNTANT, ADMINISTRATOR, CLIENT, assign_role, ensure_roles, role_of

User = get_user_model()
STRONG = "Kuat-sekali-2026"
TOKEN = "test-access-token-1234567890"


class CmsClientMixin:
    """The CMS has no account login: only the access token unlocks it."""

    def unlock(self, client=None):
        client = client or self.client
        session = client.session
        session[SESSION_KEY] = {"fp": token_fingerprint(), "until": time.time() + 3600}
        session.save()


@override_settings(ADMIN_ACCESS_TOKEN=TOKEN)
class CmsAccountTests(CmsClientMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        ensure_roles()
        cls.owner = User.objects.create_superuser("owner", "owner@example.com", STRONG)

    def setUp(self):
        self.unlock()

    def create(self, **overrides):
        data = {
            "username": "budi", "first_name": "Budi", "last_name": "Santoso", "email": "budi@example.com",
            "role": CLIENT, "is_active": "on", "password_mode": "manual", "password1": STRONG, "password2": STRONG,
            **overrides,
        }
        return self.client.post(reverse("portal_users:create"), data)

    def test_create_client_account_without_cms_login(self):
        response = self.create()
        self.assertRedirects(response, reverse("portal_users:list"))
        budi = User.objects.get(username="budi")
        self.assertEqual(role_of(budi), CLIENT)
        self.assertFalse(budi.is_staff)
        event = SecurityEventLog.objects.get(event_type="user.created")
        self.assertIsNone(event.actor)
        self.assertEqual(event.details["via"], "cms")
        self.assertContains(self.client.get(reverse("portal_users:list")), "CMS (token)")

        # The new client logs in to the portal; the portal has no link to the CMS.
        portal = self.client_class()
        response = portal.post(reverse("portal-login"), {"username": "budi", "password": STRONG, "next": "/ledger/"})
        self.assertRedirects(response, reverse("ledger:dashboard"))
        dashboard = portal.get(reverse("ledger:dashboard"))
        self.assertNotContains(dashboard, reverse("portal_users:control"))
        self.assertNotContains(dashboard, "Pusat Kendali")
        # View-only: forbidden ledger actions show the branded 403 page.
        response = portal.post(reverse("ledger:journal-post", args=[uuid.uuid4()]))
        self.assertContains(response, "Anda tidak memiliki izin", status_code=403)

    def test_generated_password_is_shown_once_and_roles_apply(self):
        response = self.create(username="sari", email="", password_mode="generate", password1="", password2="", role=ACCOUNTANT)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        sari = User.objects.get(username="sari")
        self.assertTrue(sari.check_password(response.context["password"]))
        self.assertTrue(sari.is_staff)
        self.assertTrue(sari.has_perm("ledger.change_journalheader"))
        admin = User.objects.create_user("adm", password=STRONG)
        assign_role(admin, ADMINISTRATOR)
        admin = User.objects.get(pk=admin.pk)
        # Portal administrators no longer manage accounts or agents: that lives only in the CMS.
        self.assertFalse(admin.has_perm("auth.change_user"))
        self.assertFalse(admin.has_perm("ai_engine.change_agentprofile"))
        self.assertTrue(admin.has_perm("accounts.change_account"))

    def test_validation(self):
        self.create()
        self.assertContains(self.create(email="other@example.com"), "sudah dipakai")
        response = self.create(username="weak", email="weak@example.com", password1="123", password2="123")
        self.assertContains(response, "terlalu singkat")
        self.assertFalse(User.objects.filter(username="weak").exists())

    def test_edit_role_and_deactivate_blocks_portal_login(self):
        self.create()
        budi = User.objects.get(username="budi")
        response = self.client.post(reverse("portal_users:edit", args=[budi.pk]), {
            "username": "budi", "first_name": "Budi", "last_name": "S", "email": "budi@example.com", "role": ACCOUNTANT, "is_active": "on",
        })
        self.assertRedirects(response, reverse("portal_users:list"))
        self.assertEqual(role_of(User.objects.get(pk=budi.pk)), ACCOUNTANT)
        self.client.post(reverse("portal_users:toggle-active", args=[budi.pk]))
        self.assertFalse(User.objects.get(pk=budi.pk).is_active)
        portal = self.client_class()
        portal.post(reverse("portal-login"), {"username": "budi", "password": STRONG})
        self.assertEqual(portal.get(reverse("ledger:dashboard")).status_code, 302)

    def test_reset_password_signs_out_portal_sessions(self):
        self.create()
        budi = User.objects.get(username="budi")
        portal = self.client_class()
        portal.force_login(budi)
        self.assertEqual(portal.get(reverse("ledger:dashboard")).status_code, 200)
        response = self.client.post(reverse("portal_users:reset-password", args=[budi.pk]), {"password_mode": "generate"})
        self.assertTrue(User.objects.get(pk=budi.pk).check_password(response.context["password"]))
        self.assertEqual(portal.get(reverse("ledger:dashboard")).status_code, 302)

    def test_last_superuser_is_protected(self):
        response = self.client.post(reverse("portal_users:toggle-active", args=[self.owner.pk]), follow=True)
        self.assertContains(response, "satu-satunya super admin")
        self.client.post(reverse("portal_users:delete", args=[self.owner.pk]))
        self.assertTrue(User.objects.filter(pk=self.owner.pk, is_active=True).exists())

    def test_delete_unused_account_but_protect_accounts_with_history(self):
        self.create()
        response = self.client.post(reverse("portal_users:delete", args=[User.objects.get(username="budi").pk]), follow=True)
        self.assertContains(response, "berhasil dihapus")
        self.create(username="lama", email="lama@example.com")
        lama = User.objects.get(username="lama")
        SecurityEventLog.objects.create(actor=lama, event_type="auth.login")
        response = self.client.post(reverse("portal_users:delete", args=[lama.pk]), follow=True)
        self.assertContains(response, "Nonaktifkan akun ini")

    def test_list_filters(self):
        self.create()
        self.create(username="andi", first_name="Andi", last_name="Wijaya", email="andi@example.com", role=ACCOUNTANT)
        response = self.client.get(reverse("portal_users:list"), {"role": ACCOUNTANT})
        self.assertEqual([u.username for u in response.context["users"]], ["andi"])
        response = self.client.get(reverse("portal_users:list"), {"q": "budi"})
        self.assertEqual([u.username for u in response.context["users"]], ["budi"])

    def test_portal_user_changes_own_password(self):
        portal = self.client_class()
        portal.force_login(self.owner)
        response = portal.post(reverse("account-password"), {
            "old_password": STRONG, "new_password1": "Sandi-baru-2026!", "new_password2": "Sandi-baru-2026!",
        }, follow=True)
        self.assertContains(response, "berhasil diganti")
        self.assertEqual(portal.get(reverse("ledger:dashboard")).status_code, 200)


@override_settings(ADMIN_ACCESS_TOKEN=TOKEN)
class CmsAgentTests(CmsClientMixin, TestCase):
    def setUp(self):
        self.unlock()

    def test_overview_layout_is_separate_from_portal(self):
        response = self.client.get(reverse("portal_users:control"))
        self.assertContains(response, "CMS INTERNAL")
        self.assertContains(response, "Kunci CMS")
        for label in ("Akun portal", "Agen AI", "Aktivitas", "Pembaca rekening koran", "Pemeta akun COA", "Perangkum &amp; rekap"):
            self.assertContains(response, label)
        self.assertNotContains(response, "Rekening Koran</a>")  # no client-portal sidebar
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_edit_agent_versions_prompt_and_restore(self):
        url = reverse("portal_users:agent-edit", args=[READER])
        response = self.client.post(url, {
            "instruction": "Read every row carefully. Treat BCA 'DB' as money out.", "is_enabled": "on",
            "model_name": "gemini-2.5-pro", "temperature": "0.10", "note": "aturan BCA",
        }, follow=True)
        self.assertContains(response, "versi 2")
        reader = AgentProfile.objects.get(process=READER)
        self.assertEqual((reader.version, reader.model_name, reader.updated_by), (2, "gemini-2.5-pro", None))
        prompt = compose_prompt("4100 - Pendapatan - revenue")
        self.assertIn("Treat BCA 'DB' as money out.", prompt)
        self.assertIn("SYSTEM CONTRACT", prompt)
        self.assertEqual(load_configs()[READER].model_name, "gemini-2.5-pro")

        response = self.client.post(url, {"instruction": "short", "model_name": "bad model!", "is_enabled": "on"})
        self.assertContains(response, "terlalu singkat")
        self.assertContains(response, "Nama model tidak valid")

        self.client.post(reverse("portal_users:agent-restore", args=[READER, 1]))
        reader.refresh_from_db()
        self.assertEqual((reader.version, reader.model_name), (3, ""))
        self.assertEqual(SecurityEventLog.objects.get(event_type="agent.restored").details["via"], "cms")

        self.client.post(reverse("portal_users:agent-edit", args=[MAPPER]), {"instruction": AgentProfile.objects.get(process=MAPPER).instruction})
        prompt = compose_prompt("4100 - Pendapatan - revenue")
        self.assertNotIn("4100 - Pendapatan", prompt)
        self.assertIn("account mapping is switched off", prompt)

    def test_disabled_agents_are_respected_by_gemini_client(self):
        AgentProfile.objects.filter(process=READER).update(is_enabled=False)
        with override_settings(GEMINI_API_KEY="k"), self.assertRaisesMessage(gemini.GeminiError, "dinonaktifkan"):
            gemini.read_bank_statement(b"%PDF", "application/pdf", "")

        AgentProfile.objects.filter(process=READER).update(is_enabled=True, temperature="0.30")
        AgentProfile.objects.filter(process__in=[MAPPER, SUMMARIZER]).update(is_enabled=False)
        answer = {"summary": "should be dropped", "transactions": [{"suggested_account_code": "4100"}]}
        payload = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(answer)}]}}]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with override_settings(GEMINI_API_KEY="k"), patch("apps.ai_engine.gemini.urlopen", return_value=response) as urlopen:
            result = gemini.read_bank_statement(b"%PDF", "application/pdf", "4100 - X - revenue")
        self.assertEqual(json.loads(urlopen.call_args.args[0].data)["generationConfig"]["temperature"], 0.3)
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["transactions"][0]["suggested_account_code"], "")

    def test_activity_filter_and_preview(self):
        SecurityEventLog.objects.create(event_type="auth.login", details={"username": "owner"})
        SecurityEventLog.objects.create(event_type="agent.updated", details={"agent": "statement_reader", "version": 2, "via": "cms"})
        response = self.client.get(reverse("portal_users:activity"), {"group": "agent"})
        self.assertEqual([e.event_type for e in response.context["page"].object_list], ["agent.updated"])
        self.assertContains(self.client.get(reverse("portal_users:agent-preview")), "SYSTEM CONTRACT")


@override_settings(ADMIN_ACCESS_TOKEN=TOKEN)
class InternalAreaGateTests(CmsClientMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        ensure_roles()
        cls.owner = User.objects.create_superuser("owner", "owner@example.com", STRONG)

    def setUp(self):
        cache.clear()

    def test_old_and_guessed_routes_do_not_exist(self):
        self.unlock()
        for path in ("/admin/", "/admin/login/", "/ledger/control/", "/ledger/control/users/", "/ledger/users/", "/ledger/pengguna/"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_cms_opens_with_token_only_no_login(self):
        url = reverse("portal_users:list")
        response = self.client.get(url)  # anonymous visitor, no portal login
        self.assertContains(response, "Masukkan token akses")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(self.client.post(url, {"access_token": "wrong"}), "tidak valid", status_code=401)
        self.assertTrue(SecurityEventLog.objects.filter(event_type="internal.unlock_failed").exists())
        self.assertRedirects(self.client.post(url, {"access_token": TOKEN}), url)
        self.assertContains(self.client.get(url), "Akun portal klien.")

    def test_a_portal_login_does_not_open_the_cms(self):
        self.client.force_login(self.owner)  # even a superuser signed in to the portal
        self.assertContains(self.client.get(reverse("portal_users:control")), "Masukkan token akses")

    def test_lock_closes_cms_but_keeps_portal_login(self):
        self.client.force_login(self.owner)
        self.unlock()
        self.assertContains(self.client.get(reverse("portal_users:control")), "CMS INTERNAL")
        self.client.post(reverse("portal_users:lock"))
        self.assertContains(self.client.get(reverse("portal_users:control")), "Masukkan token akses")
        self.assertEqual(self.client.get(reverse("ledger:dashboard")).status_code, 200)

    def test_admin_requires_token_before_login_page(self):
        url = reverse("admin:login")
        self.assertContains(self.client.get(url), "Masukkan token akses")
        self.client.post(url, {"access_token": TOKEN})
        self.assertContains(self.client.get(url), "Administrasi")

    def test_brute_force_lockout(self):
        url = reverse("portal_users:control")
        for _ in range(5):
            self.client.post(url, {"access_token": "nope"})
        self.assertContains(self.client.post(url, {"access_token": TOKEN}), "Terlalu banyak percobaan", status_code=429)

    def test_rotating_token_or_missing_token_locks_again(self):
        self.unlock()
        url = reverse("portal_users:control")
        self.assertEqual(self.client.get(url).status_code, 200)
        with override_settings(ADMIN_ACCESS_TOKEN="a-brand-new-token-value"):
            self.assertContains(self.client.get(url), "Masukkan token akses")
        with override_settings(ADMIN_ACCESS_TOKEN=""):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_public_site_and_portal_never_mention_secret_paths(self):
        control, admin = reverse("portal_users:control"), reverse("admin:index")
        self.client.force_login(self.owner)
        for page in (reverse("home"), reverse("ledger:dashboard"), reverse("ledger:journal-list")):
            body = self.client.get(page).content.decode()
            self.assertNotIn(control, body, page)
            self.assertNotIn(admin, body, page)
