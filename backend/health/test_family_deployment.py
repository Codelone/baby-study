import io
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings


class FamilyTrialAccountTests(TestCase):
    def provision(self, password="trial-password-with-enough-entropy"):
        with patch.dict(
            os.environ,
            {"FAMILY_TRIAL_USERNAME": "family-trial", "FAMILY_TRIAL_PASSWORD": password},
        ):
            output = io.StringIO()
            call_command("provision_family_trial", stdout=output)
            self.assertNotIn(password, output.getvalue())

    def test_creates_staff_without_backend_permissions_and_preserves_password_on_rerun(self):
        self.provision()
        user = get_user_model().objects.get(username="family-trial")
        original_hash = user.password
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.get_all_permissions())
        self.provision()
        user.refresh_from_db()
        self.assertEqual(user.password, original_hash)

    def test_refuses_to_replace_existing_password_or_strip_existing_permissions(self):
        self.provision()
        with self.assertRaises(CommandError):
            self.provision("different-password-with-enough-entropy")
        user = get_user_model().objects.get(username="family-trial")
        user.user_permissions.add(Permission.objects.first())
        with self.assertRaises(CommandError):
            self.provision()
        self.assertTrue(user.user_permissions.exists())

    @override_settings(
        ALLOWED_HOSTS=["trial.trycloudflare.com"],
        CSRF_TRUSTED_ORIGINS=["https://trial.trycloudflare.com"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        SECURE_SSL_REDIRECT=True,
        SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True,
    )
    def test_https_admin_login_and_authenticated_write_require_csrf(self):
        from django.test import Client

        self.provision()
        client = Client(enforce_csrf_checks=True)
        headers = {"HTTP_HOST": "trial.trycloudflare.com", "HTTP_X_FORWARDED_PROTO": "https"}
        response = client.get("/admin/login/?next=/", **headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.cookies["csrftoken"]["secure"])
        response = client.post(
            "/admin/login/?next=/",
            {
                "username": "family-trial",
                "password": "trial-password-with-enough-entropy",
                "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
                "next": "/",
            },
            HTTP_ORIGIN="https://trial.trycloudflare.com",
            **headers,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.cookies["sessionid"]["secure"])
        self.assertEqual(client.get("/baby/study/books", **headers).status_code, 200)
        from catalog.models import Book

        book = Book.objects.first()
        path = f"/baby/study/shelf/{book.id}"
        payload = {"ownership_status": "owned", "reading_status": "unread"}
        self.assertEqual(
            client.put(path, payload, content_type="application/json", **headers).status_code,
            403,
        )
        response = client.put(
            path,
            payload,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
            HTTP_ORIGIN="https://trial.trycloudflare.com",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            client.put(
                path,
                payload,
                content_type="application/json",
                HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
                HTTP_ORIGIN="https://untrusted.example",
                **headers,
            ).status_code,
            403,
        )
