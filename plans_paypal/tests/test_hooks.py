from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from model_bakery import baker
from paypal.standard.models import ST_PP_COMPLETED
from plans.base.models import AbstractRecurringUserPlan
from plans.models import Invoice, Order
from plans.taxation import TaxationPolicy

from plans_paypal.hooks import get_custom_data, parse_custom, receive_ipn


class StubTaxationPolicy(TaxationPolicy):
    """Test policy: no VIES/TEDB network, rate configurable per test."""

    rate = Decimal("25.5")
    successful = True

    @classmethod
    def get_tax_rate(cls, tax_id, country_code, request=None):
        return cls.rate, cls.successful


STUB_POLICY = "plans_paypal.tests.test_hooks.StubTaxationPolicy"

ISSUER = {
    "issuer_name": "Foo bar company",
    "issuer_street": "Foo",
    "issuer_city": "Bar",
    "issuer_zipcode": "12345",
    "issuer_country": "CZ",
    "issuer_tax_number": "CZ 12345678",
}


class HooksTests(TestCase):
    def test_receive_ipn_no_subscription(self):
        ipn = baker.make("PayPalIPN")
        paypal_payment = receive_ipn(ipn)
        self.assertEqual(paypal_payment, None)

    @patch("plans_paypal.hooks.logger")
    def test_get_custom_data_empty_string_no_logging(self, mock_logger):
        """Empty custom is a known PayPal pattern, should not log an error."""
        ipn = baker.make("PayPalIPN", custom="", item_number="12345")
        result = get_custom_data(ipn)
        self.assertEqual(result, {"first_order_id": "12345"})
        mock_logger.exception.assert_not_called()

    @patch("plans_paypal.hooks.logger")
    def test_get_custom_data_whitespace_no_logging(self, mock_logger):
        ipn = baker.make("PayPalIPN", custom="  ", item_number="67890")
        result = get_custom_data(ipn)
        self.assertEqual(result, {"first_order_id": "67890"})
        mock_logger.exception.assert_not_called()

    @patch("plans_paypal.hooks.logger")
    def test_get_custom_data_garbage_still_logs(self, mock_logger):
        """Non-empty unparseable custom data should still log for investigation."""
        ipn = baker.make("PayPalIPN", custom="not-valid-python", item_number="99999")
        result = get_custom_data(ipn)
        self.assertEqual(result, {"first_order_id": "99999"})
        mock_logger.exception.assert_called_once()

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

    def test_receive_ipn_pending_records_payment(self):
        # A pending subscription payment (eCheck clearing) is in flight,
        # not declined: it must leave a visible record, arm nothing, and
        # complete nothing -- the follow-up COMPLETED IPN does that.
        from paypal.standard.models import ST_PP_PENDING

        from plans_paypal.models import PayPalPayment

        user_plan = baker.make("UserPlan")
        order = baker.make("Order", user=user_plan.user, amount=100)
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_PENDING,
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            "}",
        )

        paypal_payment = receive_ipn(ipn)

        self.assertEqual(paypal_payment.paypal_ipn, ipn)
        self.assertEqual(paypal_payment.order, order)
        self.assertEqual(PayPalPayment.objects.count(), 1)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS.NEW)
        user_plan.refresh_from_db()
        self.assertFalse(hasattr(user_plan, "recurring"))

    def test_receive_ipn_completed_rolls_back_atomically(self):
        # A failure inside complete_order() must not leave the earlier
        # writes committed (armed RecurringUserPlan + PayPalPayment row
        # for an uncompleted order): PayPal retries the IPN, and the
        # leftover state made every retry die as a duplicate.
        from plans_paypal.models import PayPalPayment

        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        order = baker.make("Order", user=user, amount=100)
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

        with patch.object(Order, "complete_order", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                receive_ipn(ipn)

        self.assertFalse(PayPalPayment.objects.exists())
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

    def _renewal_ipn(self, user_plan, order, pricing, mc_gross):
        return baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_COMPLETED,
            receiver_email="fake@email.com",
            mc_gross=mc_gross,
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            f"'pricing_id': {pricing.id},"
            f"'plan_id': {order.plan.id},"
            "}",
        )

    def _make_renewal(self, tax, amount, country="FI", billing_info=True):
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        baker.make("RecurringUserPlan", user_plan=user_plan)
        order = baker.make(
            "Order", user=user, status=Order.STATUS.COMPLETED, tax=tax, amount=amount
        )
        if billing_info:
            baker.make("BillingInfo", user=user, country=country)
        pricing = baker.make("Pricing")
        return user, user_plan, order, pricing

    @override_settings(PLANS_TAXATION_POLICY=STUB_POLICY, PLANS_INVOICE_ISSUER=ISSUER)
    def test_receive_ipn_renewal_recalculates_tax_within_fixed_gross(self):
        """
        VAT rate changed since the first order (Finland 24 -> 25.5).
        The renewal must carry the current rate, recomputed top-down from
        the fixed gross PayPal charges: total stays, net shrinks.
        """
        user, user_plan, order, pricing = self._make_renewal(
            tax=24, amount=Decimal("11.18")
        )
        self.assertEqual(order.total(), Decimal("13.86"))
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("13.86"))

        paypal_payment = receive_ipn(ipn)

        new_order = paypal_payment.order
        self.assertNotEqual(new_order, order)
        self.assertEqual(new_order.tax, Decimal("25.5"))
        self.assertEqual(new_order.amount, Decimal("11.04"))
        self.assertEqual(new_order.total(), Decimal("13.86"))
        user.userplan.refresh_from_db()
        self.assertEqual(user.userplan.recurring.tax, Decimal("25.5"))
        invoice = Invoice.objects.get(type=Invoice.INVOICE_TYPES.INVOICE)
        self.assertEqual(invoice.total, Decimal("13.86"))
        self.assertEqual(invoice.total_net, Decimal("11.04"))
        self.assertEqual(invoice.tax, Decimal("25.5"))

    @override_settings(PLANS_TAXATION_POLICY=STUB_POLICY, PLANS_INVOICE_ISSUER=ISSUER)
    def test_receive_ipn_renewal_reverse_charge_gets_full_gross_as_net(self):
        """A now VIES-valid company gets reverse charge: tax None, net = gross."""
        user, user_plan, order, pricing = self._make_renewal(tax=19, amount=100)
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("119.00"))

        with patch.object(StubTaxationPolicy, "rate", None):
            paypal_payment = receive_ipn(ipn)

        new_order = paypal_payment.order
        self.assertIsNone(new_order.tax)
        self.assertEqual(new_order.amount, Decimal("119.00"))
        self.assertEqual(new_order.total(), Decimal("119.00"))

    @override_settings(PLANS_TAXATION_POLICY=STUB_POLICY, PLANS_INVOICE_ISSUER=ISSUER)
    def test_receive_ipn_renewal_keeps_copied_tax_when_tax_lookup_fails(self):
        """VIES/TEDB failure: keep the values copied from the first order."""
        user, user_plan, order, pricing = self._make_renewal(
            tax=24, amount=Decimal("11.18")
        )
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("13.86"))

        with patch.object(StubTaxationPolicy, "successful", False):
            paypal_payment = receive_ipn(ipn)

        new_order = paypal_payment.order
        self.assertEqual(new_order.tax, Decimal("24"))
        self.assertEqual(new_order.amount, Decimal("11.18"))

    @override_settings(PLANS_TAXATION_POLICY=STUB_POLICY, PLANS_INVOICE_ISSUER=ISSUER)
    def test_receive_ipn_renewal_keeps_copied_tax_without_billinginfo(self):
        """No BillingInfo -> no country to recalculate for: keep copied values."""
        user, user_plan, order, pricing = self._make_renewal(
            tax=24, amount=Decimal("11.18"), billing_info=False
        )
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("13.86"))

        paypal_payment = receive_ipn(ipn)

        new_order = paypal_payment.order
        self.assertEqual(new_order.tax, Decimal("24"))
        self.assertEqual(new_order.amount, Decimal("11.18"))

    @override_settings(PLANS_TAXATION_POLICY=STUB_POLICY, PLANS_INVOICE_ISSUER=ISSUER)
    def test_receive_ipn_renewal_inexact_gross_picks_closest_total(self):
        """
        No cent-exact net exists for gross 8.90 at 21% (7.35 -> 8.89,
        7.36 -> 8.91). The closest total wins; on a tie the lower one,
        so the invoice never exceeds what was received.
        """
        user, user_plan, order, pricing = self._make_renewal(
            tax=None, amount=Decimal("8.90"), country="LV"
        )
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("8.90"))

        with patch.object(StubTaxationPolicy, "rate", Decimal("21")):
            paypal_payment = receive_ipn(ipn)

        new_order = paypal_payment.order
        self.assertEqual(new_order.tax, Decimal("21"))
        self.assertEqual(new_order.amount, Decimal("7.35"))
        self.assertEqual(new_order.total(), Decimal("8.89"))

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
