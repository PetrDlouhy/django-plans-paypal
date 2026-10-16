from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class MigrationsTest(TestCase):
    def test_models_match_shipped_migrations(self):
        """Django 6.0 made BigAutoField the default primary key type.

        Without a pinned ``default_auto_field`` the app's models drift from
        its migrations, and ``makemigrations`` in a project writes a new
        migration into the installed package.
        """
        out = StringIO()
        call_command("makemigrations", "plans_paypal", "--dry-run", stdout=out)
        self.assertEqual(out.getvalue(), "No changes detected in app 'plans_paypal'\n")
