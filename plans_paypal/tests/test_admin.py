from django.core import checks
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from plans_paypal.models import PayPalPayment


class PayPalPaymentAdminTests(TestCase):
    def setUp(self):
        self.superuser = baker.make("User", is_staff=True, is_superuser=True)
        self.client.force_login(self.superuser)

    def test_no_admin_system_check_errors(self):
        """
        The `__` paths in list_display need RelatedFieldAdmin - plain
        ModelAdmin raises admin.E108 for them on Django < 5.1.
        """
        errors = checks.run_checks(app_configs=None)
        self.assertEqual(
            [error for error in errors if error.id.startswith("admin.")], []
        )

    def test_changelist_renders_related_columns(self):
        payment = baker.make(
            PayPalPayment,
            paypal_ipn=baker.make(
                "PayPalIPN",
                txn_id="TXN123456",
                payment_status="Completed",
                payer_email="payer@example.com",
            ),
        )
        response = self.client.get(
            reverse("admin:plans_paypal_paypalpayment_changelist")
        )
        self.assertContains(response, "TXN123456")
        self.assertContains(response, "Completed")
        self.assertContains(response, "payer@example.com")
        self.assertContains(response, str(payment.order))
