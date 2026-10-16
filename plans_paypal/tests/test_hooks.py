from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from model_bakery import baker
from paypal.standard.models import (
    ST_PP_COMPLETED,
    ST_PP_DENIED,
    ST_PP_FAILED,
    ST_PP_PENDING,
    ST_PP_REFUNDED,
    ST_PP_REVERSED,
)
from plans.base.models import AbstractRecurringUserPlan
from plans.models import Invoice, Order, RecurringUserPlan
from plans.taxation import TaxationPolicy

from plans_paypal.hooks import (
    create_new_order,
    get_custom_data,
    parse_custom,
    receive_ipn,
)
from plans_paypal.models import PayPalPayment


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
        order = baker.make("Order", user=user, amount=100, currency="USD")
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
        order = baker.make("Order", user=user, amount=100, currency="USD")
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
            "Order",
            user=user,
            status=Order.STATUS.COMPLETED,
            amount=100,
            currency="USD",
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
            "Order",
            user=user,
            status=Order.STATUS.COMPLETED,
            tax=12,
            amount=100,
            currency="USD",
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
        self.assertEqual(paypal_payment.order.gross_amount, Decimal("112.00"))
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
            "Order",
            user=user,
            status=Order.STATUS.COMPLETED,
            tax=12,
            amount=100,
            currency="USD",
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

    def _renewal_ipn(self, user_plan, order, pricing, mc_gross, mc_currency="USD"):
        return baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_COMPLETED,
            receiver_email="fake@email.com",
            mc_gross=mc_gross,
            mc_currency=mc_currency,
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            f"'pricing_id': {pricing.id},"
            f"'plan_id': {order.plan.id},"
            "}",
        )

    def _make_renewal(
        self, tax, amount, country="FI", billing_info=True, currency="USD"
    ):
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        baker.make("RecurringUserPlan", user_plan=user_plan)
        order = baker.make(
            "Order",
            user=user,
            status=Order.STATUS.COMPLETED,
            tax=tax,
            amount=amount,
            currency=currency,
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
        self.assertEqual(new_order.gross_amount, Decimal("13.86"))
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
    def test_receive_ipn_renewal_unreachable_gross_is_stored_and_split_top_down(
        self,
    ):
        """
        No net cent value reaches gross 8.90 at 21% (7.35 -> 8.89,
        7.36 -> 8.91). The charged gross is stored as the order total and
        the net derived from it by the coefficient method, so the invoice
        carries exactly what PayPal charged.
        """
        user, user_plan, order, pricing = self._make_renewal(
            tax=None, amount=Decimal("8.90"), country="LV"
        )
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("8.90"))

        with patch.object(StubTaxationPolicy, "rate", Decimal("21")):
            paypal_payment = receive_ipn(ipn)

        new_order = paypal_payment.order
        self.assertEqual(new_order.tax, Decimal("21"))
        self.assertEqual(new_order.gross_amount, Decimal("8.90"))
        self.assertEqual(new_order.amount, Decimal("7.36"))
        self.assertEqual(new_order.tax_total(), Decimal("1.54"))
        self.assertEqual(new_order.total(), Decimal("8.90"))
        invoice = Invoice.objects.get(type=Invoice.INVOICE_TYPES.INVOICE)
        self.assertEqual(invoice.total, Decimal("8.90"))
        self.assertEqual(invoice.total_net, Decimal("7.36"))
        self.assertEqual(invoice.tax_total, Decimal("1.54"))

    @override_settings(PLANS_TAXATION_POLICY=STUB_POLICY, PLANS_INVOICE_ISSUER=ISSUER)
    def test_receive_ipn_renewal_keeps_copied_tax_with_empty_country(self):
        """BillingInfo without a country cannot be recalculated for."""
        user, user_plan, order, pricing = self._make_renewal(
            tax=24, amount=Decimal("11.18"), country=""
        )
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("13.86"))

        paypal_payment = receive_ipn(ipn)

        new_order = paypal_payment.order
        self.assertEqual(new_order.tax, Decimal("24"))
        self.assertEqual(new_order.amount, Decimal("11.18"))

    @override_settings(PLANS_INVOICE_ISSUER=ISSUER)
    @patch("plans_paypal.hooks.logger")
    def test_receive_ipn_renewal_empty_custom_falls_back_to_item_number(
        self, mock_logger
    ):
        """
        PayPal sometimes sends an empty `custom` field. The order is then
        found via item_number and plan/pricing fall back to the user's
        current plan and the first order's pricing.
        """
        user, user_plan, order, pricing = self._make_renewal(
            tax=12, amount=100, billing_info=False
        )
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_COMPLETED,
            receiver_email="fake@email.com",
            mc_gross=Decimal("112.00"),
            custom="",
            item_number=str(order.id),
        )

        paypal_payment = receive_ipn(ipn)

        new_order = paypal_payment.order
        self.assertNotEqual(new_order, order)
        self.assertEqual(new_order.plan, user_plan.plan)
        self.assertEqual(new_order.pricing, order.pricing)
        self.assertEqual(new_order.tax, Decimal("12"))
        mock_logger.exception.assert_called_once()

    @override_settings(PLANS_INVOICE_ISSUER=ISSUER)
    def test_receive_ipn_accepts_amount_matching_recurring_plan(self):
        """
        A subscription amount legitimately updated on PayPal's side (e.g.
        after a VAT change) must be accepted when it matches the armed
        RecurringUserPlan expectation, even though it differs from the
        first order's total.
        """
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        # Expectation updated to net 100 at 21% -> total 121.00
        baker.make(
            "RecurringUserPlan",
            user_plan=user_plan,
            amount=Decimal("100"),
            tax=Decimal("21"),
        )
        order = baker.make(
            "Order",
            user=user,
            status=Order.STATUS.COMPLETED,
            tax=19,
            amount=100,
            currency="USD",
        )
        pricing = baker.make("Pricing")
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("121.00"))

        paypal_payment = receive_ipn(ipn)

        new_order = paypal_payment.order
        self.assertNotEqual(new_order, order)
        self.assertEqual(new_order.tax, Decimal("21"))
        self.assertEqual(new_order.amount, Decimal("100"))
        self.assertEqual(new_order.gross_amount, Decimal("121.00"))
        self.assertEqual(new_order.total(), Decimal("121.00"))

    @override_settings(PLANS_INVOICE_ISSUER=ISSUER)
    def test_receive_ipn_copies_first_order_when_paypal_charged_its_total(self):
        """
        The RecurringUserPlan expectation was updated but PayPal still
        charged the first order's total: the order must carry the first
        order's tax and net, the values the charge actually corresponds to.
        """
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        baker.make(
            "RecurringUserPlan",
            user_plan=user_plan,
            amount=Decimal("100"),
            tax=Decimal("21"),
        )
        order = baker.make(
            "Order",
            user=user,
            status=Order.STATUS.COMPLETED,
            tax=19,
            amount=100,
            currency="USD",
        )
        pricing = baker.make("Pricing")
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("119.00"))

        paypal_payment = receive_ipn(ipn)

        new_order = paypal_payment.order
        self.assertEqual(new_order.tax, Decimal("19"))
        self.assertEqual(new_order.amount, Decimal("100"))
        self.assertEqual(new_order.gross_amount, Decimal("119.00"))
        self.assertEqual(new_order.total(), Decimal("119.00"))

    def test_create_new_order_without_recurring_copies_first_order(self):
        """No armed RecurringUserPlan at all: the first order is the only
        candidate."""
        user, user_plan, order, pricing = self._make_renewal(
            tax=19, amount=100, billing_info=False
        )
        user_plan.recurring.delete()
        user_plan = user_plan.__class__.objects.get(pk=user_plan.pk)
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("119.00"))

        new_order = create_new_order(order, user_plan, ipn, get_custom_data(ipn))

        self.assertEqual(new_order.tax, Decimal("19"))
        self.assertEqual(new_order.amount, Decimal("100"))
        self.assertEqual(new_order.gross_amount, Decimal("119.00"))

    def test_create_new_order_rejects_gross_matching_no_copy(self):
        """
        Neither the expectation nor the first order explains the charged
        gross (only reachable by calling create_new_order outside the
        IPN validation): the order is not saved rather than invoiced for
        an amount that was not charged.
        """
        user, user_plan, order, pricing = self._make_renewal(
            tax=19, amount=100, billing_info=False
        )
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("150.00"))

        with self.assertRaises(ValidationError):
            create_new_order(order, user_plan, ipn, get_custom_data(ipn))

        self.assertEqual(Order.objects.count(), 1)

    def _first_payment_ipn(self, order_currency, mc_currency, **order_fields):
        user = baker.make("User")
        user_plan = baker.make("UserPlan", user=user)
        order = baker.make(
            "Order", user=user, amount=100, currency=order_currency, **order_fields
        )
        pricing = baker.make("Pricing")
        ipn = self._renewal_ipn(
            user_plan, order, pricing, Decimal("100.00"), mc_currency
        )
        return order, ipn

    def _assert_first_payment_accepted(self, order_currency, mc_currency):
        order, ipn = self._first_payment_ipn(order_currency, mc_currency)

        paypal_payment = receive_ipn(ipn)

        self.assertEqual(paypal_payment.order, order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS.COMPLETED)

    def _assert_first_payment_refused(self, order_currency, mc_currency):
        order, ipn = self._first_payment_ipn(order_currency, mc_currency)

        with self.assertRaisesRegex(Exception, "Received currency doesn't match"):
            receive_ipn(ipn)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS.NEW)
        self.assertFalse(PayPalPayment.objects.exists())
        self.assertFalse(RecurringUserPlan.objects.exists())

    def _renewal_payment_ipn(self, order_currency, mc_currency):
        user, user_plan, order, pricing = self._make_renewal(
            tax=None, amount=100, billing_info=False, currency=order_currency
        )
        return self._renewal_ipn(
            user_plan, order, pricing, Decimal("100.00"), mc_currency
        )

    def _assert_renewal_accepted(self, order_currency, mc_currency):
        ipn = self._renewal_payment_ipn(order_currency, mc_currency)

        paypal_payment = receive_ipn(ipn)

        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(paypal_payment.order.status, Order.STATUS.COMPLETED)
        return paypal_payment.order

    def _assert_renewal_refused(self, order_currency, mc_currency):
        ipn = self._renewal_payment_ipn(order_currency, mc_currency)

        with self.assertRaisesRegex(Exception, "Received currency doesn't match"):
            receive_ipn(ipn)

        self.assertEqual(Order.objects.count(), 1)
        self.assertFalse(PayPalPayment.objects.exists())

    def test_receive_ipn_accepts_first_payment_in_the_order_currency(self):
        self._assert_first_payment_accepted("EUR", "EUR")

    def test_receive_ipn_rejects_first_payment_in_another_currency(self):
        """
        The subscribe button is a client-side form: a buyer can edit its
        currency_code and pay a 100 USD order with 100 JPY. The postback
        only proves PayPal sent the IPN, not what it charged.
        """
        self._assert_first_payment_refused("USD", "JPY")

    def test_receive_ipn_accepts_renewal_in_the_order_currency(self):
        self._assert_renewal_accepted("EUR", "EUR")

    def test_receive_ipn_rejects_renewal_in_another_currency(self):
        """A renewal would otherwise be invoiced as a new order in JPY."""
        self._assert_renewal_refused("USD", "JPY")

    def test_receive_ipn_rejects_usd_payment_for_a_non_usd_order(self):
        """
        Buttons before 1.7.0 charged USD whatever the order's currency, so
        a subscription created then on a non-USD site keeps paying USD.
        No legacy fallback: it is refused like any other currency.
        """
        self._assert_first_payment_refused("EUR", "USD")

    def test_receive_ipn_rejects_usd_renewal_for_a_non_usd_order(self):
        self._assert_renewal_refused("EUR", "USD")

    def test_receive_ipn_first_payment_currency_ignores_case(self):
        self._assert_first_payment_accepted("EUR", "eur")

    def test_receive_ipn_renewal_currency_ignores_case(self):
        self._assert_renewal_accepted("eur", "Eur")

    def test_receive_ipn_renewal_is_invoiced_in_the_subscription_currency(self):
        renewal = self._assert_renewal_accepted("EUR", "eur")

        self.assertEqual(renewal.currency, "EUR")
        self.assertEqual(RecurringUserPlan.objects.get().currency, "EUR")

    def test_create_new_order_does_not_take_the_currency_from_the_ipn(self):
        """create_new_order() is public: it may run without the IPN checks."""
        user, user_plan, order, pricing = self._make_renewal(
            tax=None, amount=100, billing_info=False, currency="USD"
        )
        ipn = self._renewal_ipn(
            user_plan, order, pricing, Decimal("100.00"), mc_currency="JPY"
        )

        renewal = create_new_order(order, user_plan, ipn, get_custom_data(ipn))

        self.assertEqual(renewal.currency, "USD")

    def test_receive_ipn_accepts_zero_decimal_gross(self):
        """PayPal reports 1210 JPY without decimals; the order total is 1210.00."""
        user, user_plan, order, pricing = self._make_renewal(
            tax=21, amount=1000, billing_info=False, currency="JPY"
        )
        ipn = self._renewal_ipn(
            user_plan, order, pricing, Decimal("1210"), mc_currency="JPY"
        )

        renewal = receive_ipn(ipn).order

        self.assertEqual(renewal.currency, "JPY")
        self.assertEqual(renewal.total(), Decimal("1210"))
        self.assertEqual(renewal.tax, Decimal("21"))

    @override_settings(PLANS_TAXATION_POLICY=STUB_POLICY, PLANS_INVOICE_ISSUER=ISSUER)
    def test_receive_ipn_renewal_tax_split_keeps_zero_decimal_gross(self):
        """
        The net is derived on django-plans' cent grid in every currency, so
        a JPY renewal's net and tax can carry sen; the charged gross stays
        exact (Order.validate_gross_amount() allows no whole-yen net here).
        """
        user, user_plan, order, pricing = self._make_renewal(
            tax=21, amount=1000, currency="JPY"
        )
        ipn = self._renewal_ipn(
            user_plan, order, pricing, Decimal("1210"), mc_currency="JPY"
        )

        renewal = receive_ipn(ipn).order

        self.assertEqual(renewal.tax, StubTaxationPolicy.rate)
        self.assertEqual(renewal.gross_amount, Decimal("1210"))
        self.assertEqual(renewal.total(), Decimal("1210"))
        self.assertEqual(renewal.amount + renewal.tax_total(), Decimal("1210"))

    def test_receive_ipn_pending_payment_in_another_currency_completes_nothing(
        self,
    ):
        """The attempt is recorded (its IPN shows JPY); the order is untouched."""
        user_plan = baker.make("UserPlan")
        order = baker.make("Order", user=user_plan.user, amount=100, currency="USD")
        pricing = baker.make("Pricing")
        ipn = self._renewal_ipn(
            user_plan, order, pricing, Decimal("100.00"), mc_currency="JPY"
        )
        ipn.payment_status = ST_PP_PENDING

        paypal_payment = receive_ipn(ipn)

        self.assertEqual(paypal_payment.order, order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS.NEW)
        self.assertFalse(RecurringUserPlan.objects.exists())

    def test_receive_ipn_other_subscription_ipns_alter_no_order(self):
        """
        Only a completed subscr_payment completes or creates an order;
        signup (amount3 + mc_currency), failure, end of term, modify and
        refund adjustments (no txn_type) are ignored, whatever their currency.
        """
        for txn_type in (
            "subscr_signup",
            "subscr_failed",
            "subscr_eot",
            "subscr_modify",
            "",
        ):
            with self.subTest(txn_type=txn_type):
                order, ipn = self._first_payment_ipn("USD", "JPY")
                ipn.txn_type = txn_type

                self.assertIsNone(receive_ipn(ipn))

                order.refresh_from_db()
                self.assertEqual(order.status, Order.STATUS.NEW)
                self.assertFalse(PayPalPayment.objects.exists())
                self.assertFalse(RecurringUserPlan.objects.exists())

    @patch("plans_paypal.hooks.logger")
    def test_receive_ipn_unfinished_subscription_payment_alters_no_order(
        self, mock_logger
    ):
        """Refunds and reversals are not handled: no order is touched."""
        for payment_status in (
            ST_PP_REFUNDED,
            ST_PP_REVERSED,
            ST_PP_DENIED,
            ST_PP_FAILED,
        ):
            with self.subTest(payment_status=payment_status):
                order, ipn = self._first_payment_ipn(
                    "USD", "JPY", status=Order.STATUS.COMPLETED
                )
                ipn.payment_status = payment_status
                orders = Order.objects.count()

                self.assertIsNone(receive_ipn(ipn))

                mock_logger.error.assert_called_with(
                    "IPN with unknown status",
                    extra={"ipn_obj": ipn, "ipn_status": payment_status},
                )
                self.assertEqual(Order.objects.count(), orders)
                self.assertFalse(PayPalPayment.objects.exists())

    def test_receive_ipn_rejects_amount_matching_neither_expectation(self):
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        baker.make(
            "RecurringUserPlan",
            user_plan=user_plan,
            amount=Decimal("100"),
            tax=Decimal("21"),
        )
        order = baker.make(
            "Order",
            user=user,
            status=Order.STATUS.COMPLETED,
            tax=19,
            amount=100,
            currency="USD",
        )
        pricing = baker.make("Pricing")
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("999.00"))

        with self.assertRaisesRegex(Exception, "Received amount doesn't match"):
            receive_ipn(ipn)

    @override_settings(PLANS_TAXATION_POLICY=STUB_POLICY, PLANS_INVOICE_ISSUER=ISSUER)
    @patch.object(StubTaxationPolicy, "get_tax_rate")
    def test_receive_ipn_renewal_company_vat_id_passed_to_policy(self, mock_rate):
        """A billing VAT ID reaches the taxation policy in full form."""
        mock_rate.return_value = (None, True)
        user, user_plan, order, pricing = self._make_renewal(
            tax=None, amount=100, billing_info=False
        )
        baker.make("BillingInfo", user=user, country="DE", tax_number="123456789")
        ipn = self._renewal_ipn(user_plan, order, pricing, Decimal("100.00"))

        receive_ipn(ipn)

        mock_rate.assert_called_with(
            "DE123456789", "DE", None
        )  # pragma: allowlist secret

    def test_receive_ipn_cancellation_without_recurring_plan(self):
        """A stale cancellation for a user with no recurring plan is a no-op."""
        user_plan = baker.make("UserPlan")
        order = baker.make("Order", user=user_plan.user, status=Order.STATUS.COMPLETED)
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_cancel",
            subscr_id=1234,
            custom="{" f"'first_order_id': {order.id}," "}",
        )

        self.assertIsNone(receive_ipn(ipn))

        user_plan.refresh_from_db()
        self.assertFalse(hasattr(user_plan, "recurring"))

    @override_settings(PAYPAL_TEST_BUSSINESS_EMAIL="sandbox@email.com")
    def test_receive_ipn_sandbox_validates_against_test_business_email(self):
        """A test IPN must be checked against the sandbox business email."""
        user_plan = baker.make("UserPlan")
        order = baker.make("Order", user=user_plan.user, amount=100)
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_COMPLETED,
            test_ipn=True,
            receiver_email="fake@email.com",
            mc_gross=Decimal("100.00"),
            custom="{" f"'first_order_id': {order.id}," "}",
        )
        with self.assertRaisesRegex(
            Exception, "doesn't match: 'fake@email.com' != 'sandbox@email.com'"
        ):
            receive_ipn(ipn)

    def test_receive_ipn_cancellation_token_mismatch_keeps_recurring(self):
        """
        A cancellation IPN with a foreign subscr_id must not delete the
        user's recurring plan (e.g. a stale IPN after re-subscribing).
        """
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        baker.make("RecurringUserPlan", user_plan=user_plan, token=1234)
        order = baker.make("Order", user=user, status=Order.STATUS.COMPLETED)
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_cancel",
            receiver_email="fake@email.com",
            subscr_id=9999,
            custom="{" f"'first_order_id': {order.id}," "}",
        )

        self.assertIsNone(receive_ipn(ipn))

        user_plan.refresh_from_db()
        self.assertTrue(hasattr(user_plan, "recurring"))
        self.assertEqual(user_plan.recurring.token, "1234")

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
