from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.api.models import ApiKey
from apps.core.testing.factories import World


class ApiTests(TestCase):
    def setUp(self):
        self.w = World()
        self.req = self.w.draft()
        self.w.act(self.req, self.w.requester, "submit")
        self.req.refresh_from_db()
        self.key, self.raw = ApiKey.issue("ERP")

    def get(self, url, key=None, **params):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {key or self.raw}"} if key is not False else {}
        return self.client.get(url, params, **headers)

    def test_requires_key(self):
        self.assertEqual(self.get(reverse("api:requests"), key=False).status_code, 401)
        self.assertEqual(self.get(reverse("api:requests"), key="cutacq_bad").status_code, 401)

    def test_revoked_key_fails(self):
        self.key.revoked_at = timezone.now()
        self.key.save()
        self.assertEqual(self.get(reverse("api:requests")).status_code, 401)

    def test_list_and_detail(self):
        data = self.get(reverse("api:requests")).json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["reference"], self.req.reference)
        detail = self.get(reverse("api:request_detail", args=[self.req.reference])).json()
        self.assertEqual(detail["items"][0]["description"], "Laptop")
        self.assertEqual(detail["routing"][0]["action"], "submit")
        self.assertEqual(len(detail["routing"][0]["hash"]), 64)
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.last_used_at)

    def test_drafts_are_not_exposed(self):
        draft = self.w.draft()
        self.assertEqual(self.get(reverse("api:requests")).json()["count"], 1)
        self.assertIsNone(draft.reference)

    def test_assets_and_filters(self):
        self.w.stock(serial="A1")
        data = self.get(reverse("api:assets"), status="in_stock").json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(self.get(reverse("api:assets"), updated_since="not-a-date").status_code, 400)

    def test_api_is_read_only(self):
        response = self.client.post(reverse("api:requests"), HTTP_AUTHORIZATION=f"Bearer {self.raw}")
        self.assertEqual(response.status_code, 405)

    def test_only_hash_is_stored(self):
        self.assertNotEqual(self.key.key_hash, self.raw)
        self.assertNotIn(self.raw, str(ApiKey.objects.values_list("key_hash", "prefix")))

    def test_create_api_key_command(self):
        out = StringIO()
        call_command("create_api_key", "Portal", stdout=out)
        raw = out.getvalue().strip().splitlines()[-1]
        self.assertIsNotNone(ApiKey.authenticate(raw))
