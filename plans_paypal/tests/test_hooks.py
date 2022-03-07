from django.test import TestCase
from model_bakery import baker
from paypal.standard.models import ST_PP_COMPLETED

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
            {"user_plan_id": 250329, "plan_id": 1,
             "pricing_id": 1, "first_order_id": 32782, "user_email": None},
        )

    def test_receive_ipn(self):
        order = baker.make("Order")
        user_plan = baker.make("UserPlan")
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            "}",
        )
        paypal_payment = receive_ipn(ipn)
        self.assertEqual(paypal_payment.paypal_ipn, ipn)
        self.assertEqual(paypal_payment.order, order)

    def test_receive_ipn_completed_email_does_not_match(self):
        order = baker.make("Order")
        user_plan = baker.make("UserPlan")
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

    def test_receive_ipn_completed(self):
        user = baker.make("User", username="foobar")
        user_plan = baker.make("UserPlan", user=user)
        order = baker.make("Order", user=user)
        order.user.save()
        pricing = baker.make("Pricing")
        ipn = baker.make(
            "PayPalIPN",
            txn_type="subscr_payment",
            payment_status=ST_PP_COMPLETED,
            receiver_email="fake@email.com",
            custom="{"
            f"'first_order_id': {order.id},"
            f"'user_plan_id': {user_plan.id},"
            f"'pricing_id': {pricing.id},"
            "}",
        )
        paypal_payment = receive_ipn(ipn)
        self.assertEqual(paypal_payment.paypal_ipn, ipn)
        self.assertEqual(paypal_payment.order, order)
