from django.test import TestCase
from django.urls import reverse
from model_bakery import baker
from plans.models import Order


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
        order = baker.make(Order, status=Order.STATUS.COMPLETED)
        self.client.force_login(order.user)
        with self.assertRaisesRegex(ValueError, r"^Invalid order status: 2"):
            self.client.get(reverse("paypal-payment-failure", args=[order.id]))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS.COMPLETED)

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
