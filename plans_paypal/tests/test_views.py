from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from model_bakery import baker
from plans.models import Order

from plans_paypal.views import PlansPayPalPaymentsForm


class PaymentFormViewTests(TestCase):
    def _get_payment_page(self, days):
        user = baker.make("User")
        baker.make("UserPlan", user=user)
        pricing = baker.make("Pricing", period=days)
        plan_pricing = baker.make("PlanPricing", pricing=pricing, price=10)
        order = baker.make(
            Order, user=user, plan=plan_pricing.plan, pricing=pricing, amount=10
        )
        self.client.force_login(user)
        return self.client.get(reverse("paypal-payment", args=[order.id]))

    def test_monthly_pricing_uses_day_unit(self):
        response = self._get_payment_page(days=30)
        form = response.context["form"]
        self.assertEqual(form.initial["t3"], "D")
        self.assertEqual(form.initial["p3"], 30)

    def test_yearly_pricing_uses_year_unit(self):
        """PayPal accepts at most 90 days / 52 weeks; a year must be Y."""
        response = self._get_payment_page(days=365)
        form = response.context["form"]
        self.assertEqual(form.initial["t3"], "Y")

    @override_settings(PAYPAL_TEST=True)
    def test_form_ignores_global_sandbox_setting(self):
        """
        Live buttons must not switch to sandbox just because PAYPAL_TEST
        is set - only the explicit test_mode_enabled flag may do that.
        """
        form = PlansPayPalPaymentsForm(initial={}, test_mode_enabled=False)
        self.assertFalse(form.test_mode())

    @override_settings(PAYPAL_TEST=True)
    def test_form_sandbox_mode_follows_global_setting_when_enabled(self):
        form = PlansPayPalPaymentsForm(initial={}, test_mode_enabled=True)
        self.assertTrue(form.test_mode())

    def test_mid_length_pricing_uses_week_unit(self):
        """
        91-363 day pricings convert to weeks. Characterizes the current
        fractional p3 (PayPal documents an integer) - do not "fix" without
        evidence live subscriptions need it changed.
        """
        response = self._get_payment_page(days=180)
        form = response.context["form"]
        self.assertEqual(form.initial["t3"], "W")
        self.assertEqual(form.initial["p3"], 180 / 7)

    @override_settings(
        PAYPAL_ENCRYPTED_FORM=True,
        PAYPAL_TEST_BUSSINESS_EMAIL="sandbox@email.com",
        PAYPAL_TEST_PRIVATE_CERT="test-priv.pem",
        PAYPAL_TEST_PUBLIC_CERT="test-pub.pem",
        PAYPAL_TEST_CERT="test-paypal.pem",
        PAYPAL_TEST_CERT_ID="TESTCERTID",
    )
    @mock.patch("plans_paypal.views.PlansPayPalEncryptedPaymentsForm")
    def test_sandbox_encrypted_form_gets_test_certificates(self, mock_form):
        user = baker.make("User")
        baker.make("UserPlan", user=user)
        pricing = baker.make("Pricing", period=30)
        plan_pricing = baker.make("PlanPricing", pricing=pricing, price=10)
        order = baker.make(
            Order, user=user, plan=plan_pricing.plan, pricing=pricing, amount=10
        )
        self.client.force_login(user)
        self.client.get(reverse("paypal-payment-sandbox", args=[order.id]))
        self.assertEqual(mock_form.call_args.kwargs["private_cert"], "test-priv.pem")
        self.assertEqual(mock_form.call_args.kwargs["cert_id"], "TESTCERTID")

    @override_settings(PAYPAL_ENCRYPTED_FORM=True)
    @mock.patch("plans_paypal.views.PlansPayPalEncryptedPaymentsForm")
    def test_live_encrypted_form_gets_no_test_certificates(self, mock_form):
        """Live encrypted buttons use the defaults, not sandbox certs."""
        self._get_payment_page(days=30)
        self.assertNotIn("private_cert", mock_form.call_args.kwargs)


class PaymentFailureViewTests(TestCase):
    def test_payment_failure_view(self):
        user = baker.make("User")
        order = baker.make(Order, user=user)
        self.client.force_login(user)
        response = self.client.get(reverse("paypal-payment-failure", args=[order.id]))
        self.assertRedirects(
            response,
            reverse("order_payment_failure", args=[order.id]),
            target_status_code=302,
        )

    def test_payment_failure_view_order_for_different_user(self):
        order = baker.make(Order, user=baker.make("User"))
        self.client.force_login(baker.make("User"))
        response = self.client.get(reverse("paypal-payment-failure", args=[order.id]))
        self.assertEqual(response.status_code, 404)

    def test_payment_failure_view_order_does_not_exist(self):
        self.client.force_login(baker.make("User"))
        response = self.client.get(reverse("paypal-payment-failure", args=[1]))
        self.assertEqual(response.status_code, 404)

    def test_payment_failure_view_order_completed(self):
        """Already-completed orders redirect to success, not failure.

        PayPal can send the buyer to ``cancel_return`` even after the IPN has
        marked the order as completed (browser back button, stale tabs, or
        edge cases in the PayPal flow). The view must not 500 in that case
        and must not flip a paid order to ``CANCELED``.
        """
        order = baker.make(Order, status=Order.STATUS.COMPLETED)
        self.client.force_login(order.user)
        with self.assertLogs("plans_paypal.views", level="WARNING") as cm:
            response = self.client.get(
                reverse("paypal-payment-failure", args=[order.id])
            )
        self.assertRedirects(
            response,
            reverse("order_payment_success", args=[order.id]),
            target_status_code=302,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS.COMPLETED)
        self.assertTrue(
            any("already-completed order" in m for m in cm.output),
            cm.output,
        )

    def test_payment_failure_view_not_logged_in(self):
        order = baker.make(Order, user=baker.make("User"))
        response = self.client.get(reverse("paypal-payment-failure", args=[order.id]))
        self.assertRedirects(
            response,
            "/accounts/login/?next="
            + reverse("paypal-payment-failure", args=[order.id]),
            target_status_code=404,
        )


class PayPalPaymentViewTests(TestCase):
    def test_paypal_payment_view(self):
        order = baker.make(
            Order,
            user=baker.make("User"),
            pricing__period=30,
            pricing__name="test pricing",
            plan__name="test plan",
        )
        baker.make("UserPlan", user=order.user)
        response = self.client.get(reverse("paypal-payment", args=[order.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "<h1>Confirm our subscribtion</h1>",
            html=True,
        )
