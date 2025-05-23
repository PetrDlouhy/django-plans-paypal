from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from model_bakery import baker
from paypal.standard.models import ST_PP_COMPLETED
from plans.base.models import AbstractRecurringUserPlan
from plans.models import Invoice, Order

from plans_paypal.hooks import parse_custom, receive_ipn


class HooksTests(TestCase):
    def test_receive_ipn_no_subscription(self):
        ipn = baker.make("PayPalIPN")
        paypal_payment = receive_ipn(ipn)
        self.assertEqual(paypal_payment, None)

    def test_parse_custom(self):
        self.assertEqual(
            parse_custom(
                '{"user_plan_id": 250329, "plan_id": 1, '
                '\'pricing_id\': 1, "first_order_id": 32782, "user_email": null}'
            ),
            {
                "user_plan_id": 250329,
                "plan_id": 1,
                "pricing_id": 1,
                "first_order_id": 32782,
                "user_email": None,
            },
        )

    @patch("plans_paypal.hooks.logger")
    def test_receive_ipn_exception(self, mock_logger):
        user_plan = baker.make("UserPlan")
        order = baker.make("Order", user=user_plan.user)
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            "}",
        )
        self.assertEqual(receive_ipn(ipn), None)
        mock_logger.error.assert_called_with(
            "IPN with unknown status", extra={"ipn_obj": ipn, "ipn_status": ""}
        )
        user_plan.refresh_from_db()
        self.assertFalse(hasattr(user_plan, "recurring"))

    def test_receive_ipn_completed_email_does_not_match(self):
        user_plan = baker.make("UserPlan")
        order = baker.make("Order", user=user_plan.user)
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_COMPLETED,
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            "}",
        )
        with self.assertRaisesRegex(
            Exception, "Returned email doesn't match: '' != 'fake@email.com'"
        ):
            receive_ipn(ipn)
        user_plan.refresh_from_db()
        self.assertFalse(hasattr(user_plan, "recurring"))

    def test_receive_ipn_completed(self):
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        order = baker.make("Order", user=user, amount=100)
        order.user.save()
        pricing = baker.make("Pricing")
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_COMPLETED,
            receiver_email="fake@email.com",
            mc_gross=100.00,
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            f"'pricing_id': {pricing.id},"
            "}",
        )
        paypal_payment = receive_ipn(ipn)
        self.assertEqual(paypal_payment.paypal_ipn, ipn)
        self.assertEqual(paypal_payment.order, order)
        user.userplan.refresh_from_db()
        self.assertEqual(user.userplan.recurring.amount, Decimal("100.00"))
        self.assertIsNone(user.userplan.recurring.tax)
        self.assertEqual(user.userplan.recurring.token, "")
        self.assertEqual(user.userplan.recurring.payment_provider, "paypal-recurring")
        self.assertEqual(
            user.userplan.recurring.renewal_triggered_by,
            AbstractRecurringUserPlan.RENEWAL_TRIGGERED_BY.OTHER,
        )
        self.assertTrue(user.userplan.recurring.token_verified)

    def test_receive_ipn_completed_order_completed(self):
        """
        Test completed IPN status when the previous order is completed
        and we need to make new order
        """
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        order = baker.make(
            "Order", user=user, status=Order.STATUS.COMPLETED, amount=100
        )
        order.user.save()
        pricing = baker.make("Pricing")
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_COMPLETED,
            receiver_email="fake@email.com",
            mc_gross=100.00,
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            f"'pricing_id': {pricing.id},"
            f"'plan_id': {order.plan.id},"
            "}",
        )
        paypal_payment = receive_ipn(ipn)
        self.assertEqual(paypal_payment.paypal_ipn, ipn)
        self.assertNotEqual(paypal_payment.order, order)
        user.userplan.refresh_from_db()
        self.assertEqual(user.userplan.recurring.amount, Decimal("100.00"))
        self.assertIsNone(user.userplan.recurring.tax)
        self.assertEqual(user.userplan.recurring.token, "")
        self.assertEqual(user.userplan.recurring.payment_provider, "paypal-recurring")
        self.assertEqual(
            user.userplan.recurring.renewal_triggered_by,
            AbstractRecurringUserPlan.RENEWAL_TRIGGERED_BY.OTHER,
        )
        self.assertTrue(user.userplan.recurring.token_verified)

    @override_settings(
        PLANS_INVOICE_ISSUER={
            "issuer_name": "Foo bar company",
            "issuer_street": "Foo",
            "issuer_city": "Bar",
            "issuer_zipcode": "12345",
            "issuer_country": "CZ",
            "issuer_tax_number": "CZ 12345678",
        }
    )
    def test_receive_ipn_renewal(self):
        """
        When a new IPN is received, new order need to be created
        based on the recurring plan.
        The amount should be taken from the IPN, tax from the original order.
        """
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        baker.make("RecurringUserPlan", user_plan=user_plan)
        order = baker.make(
            "Order", user=user, status=Order.STATUS.COMPLETED, tax=12, amount=100
        )
        baker.make("BillingInfo", user=user)
        order.user.save()
        pricing = baker.make("Pricing")
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_COMPLETED,
            receiver_email="fake@email.com",
            mc_gross=112.00,
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            f"'pricing_id': {pricing.id},"
            f"'plan_id': {order.plan.id},"
            "}",
        )
        paypal_payment = receive_ipn(ipn)
        self.assertEqual(paypal_payment.paypal_ipn, ipn)
        self.assertEqual(Order.objects.count(), 2)
        self.assertNotEqual(paypal_payment.order, order)
        self.assertEqual(paypal_payment.order.amount, 100.00)
        self.assertEqual(paypal_payment.order.tax, 12.0)
        self.assertEqual(paypal_payment.order.total(), 112.00)
        user.userplan.refresh_from_db()
        new_recurring_plan = user.userplan.recurring
        self.assertEqual(new_recurring_plan.amount, Decimal("100.00"))
        self.assertEqual(new_recurring_plan.tax, 12.0)
        self.assertEqual(new_recurring_plan.token, "")
        self.assertEqual(new_recurring_plan.payment_provider, "paypal-recurring")
        self.assertEqual(
            new_recurring_plan.renewal_triggered_by,
            AbstractRecurringUserPlan.RENEWAL_TRIGGERED_BY.OTHER,
        )
        self.assertTrue(new_recurring_plan.token_verified)
        invoice = Invoice.objects.get(type=Invoice.INVOICE_TYPES.INVOICE)
        self.assertEqual(invoice.total, 112.00)
        self.assertEqual(invoice.total_net, 100.00)
        self.assertEqual(invoice.tax_total, 12.0)
        self.assertEqual(invoice.tax, 12.0)

    def test_receive_ipn_renewal_wrong_amount(self):
        """
        When a new IPN is received, new order need to be created
        based on the recurring plan.
        Test that error is raised when the amount is different from the original order.
        """
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        baker.make("RecurringUserPlan", user_plan=user_plan)
        order = baker.make(
            "Order", user=user, status=Order.STATUS.COMPLETED, tax=12, amount=100
        )
        order.user.save()
        pricing = baker.make("Pricing")
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_COMPLETED,
            receiver_email="fake@email.com",
            mc_gross=123.45,
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            f"'pricing_id': {pricing.id},"
            "}",
        )
        with self.assertRaisesRegex(Exception, "Received amount doesn't match"):
            receive_ipn(ipn)
        user.userplan.refresh_from_db()
        self.assertIsNone(user.userplan.recurring.amount)
        self.assertIsNone(user.userplan.recurring.tax)
        self.assertIsNone(user.userplan.recurring.token)
        self.assertIsNone(user.userplan.recurring.payment_provider)
        self.assertEqual(
            user.userplan.recurring.renewal_triggered_by,
            AbstractRecurringUserPlan.RENEWAL_TRIGGERED_BY.USER,
        )
        self.assertFalse(user.userplan.recurring.token_verified)

    def test_receive_ipn_cancellation(self):
        """
        Test cancellation IPN status.
        Should delete recurring plan.
        """
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        baker.make("RecurringUserPlan", user_plan=user_plan, token=1234)
        # recurring_up = baker.make("RecurringUserPlan", user_plan=user_plan)
        order = baker.make("Order", user=user, status=Order.STATUS.COMPLETED)
        order.user.save()
        pricing = baker.make("Pricing")
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_cancel",
            receiver_email="fake@email.com",
            subscr_id=1234,
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            f"'pricing_id': {pricing.id},"
            "}",
        )
        paypal_payment = receive_ipn(ipn)
        self.assertEqual(paypal_payment, None)
        user_plan.refresh_from_db()
        self.assertFalse(hasattr(user_plan, "recurring"))
