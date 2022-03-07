import ast

from django.conf import settings
from paypal.standard.ipn.signals import valid_ipn_received
from paypal.standard.models import ST_PP_COMPLETED
from plans.models import Order, Pricing, UserPlan

from .models import PayPalPayment


def parse_custom(custom):
    custom = custom.replace("null", "None")
    return ast.literal_eval(custom)


def receive_ipn(sender, **kwargs):
    print("paypal hook")
    ipn_obj = sender
    print("Payment status: ", ipn_obj.payment_status)
    print(ipn_obj.receiver_email)
    print(ipn_obj.txn_type)

    if not (
        ipn_obj.is_subscription_cancellation() or ipn_obj.is_subscription_payment()
    ):
        # Not a subscription
        return None

    custom = parse_custom(ipn_obj.custom)
    order = Order.objects.get(pk=custom["first_order_id"])
    print("Order: ", order.id)
    user_plan = UserPlan.objects.get(pk=custom["user_plan_id"])

    if ipn_obj.is_subscription_cancellation() and hasattr(user_plan, "recurring"):
        user_plan.recurring.delete()
    elif (
        ipn_obj.is_subscription_payment() and ipn_obj.payment_status == ST_PP_COMPLETED
    ):
        # WARNING !
        # Check that the receiver email is the same we previously
        # set on the `business` field. (The user could tamper with
        # that fields on the payment form before it goes to PayPal)
        print(ipn_obj.receiver_email)
        bussiness_email = settings.PAYPAL_BUSSINESS_EMAIL
        if ipn_obj.test_ipn:
            bussiness_email = settings.PAYPAL_TEST_BUSSINESS_EMAIL
        if ipn_obj.receiver_email != bussiness_email:
            # Not a valid payment
            raise Exception(
                f"Returned email doesn't match: '{ipn_obj.receiver_email}' != '{bussiness_email}'"
            )

        # ALSO: for the same reason, you need to check the amount
        # received, `custom` etc. are all what you expect or what
        # is allowed.

        # Undertake some action depending upon `ipn_obj`.
        pricing = Pricing.objects.get(pk=custom["pricing_id"])
        if order.status == Order.STATUS.COMPLETED:
            order = Order.objects.create(
                user=user_plan.user,
                plan=user_plan.plan,
                pricing=pricing,
                amount=ipn_obj.mc_gross,
                currency=ipn_obj.mc_currency,
            )
        user_plan.set_plan_renewal(
            order,
            token=ipn_obj.subscr_id,
            payment_provider="paypal-recurring"
            + ("-sandbox" if ipn_obj.test_ipn else ""),
            has_automatic_renewal=True,
            token_verified=True,
        )
        paypal_payment = PayPalPayment.objects.create(
            paypal_ipn=ipn_obj, user_plan=user_plan, order=order
        )  # use the new order
        order.complete_order()
        return paypal_payment
    return PayPalPayment.objects.create(
        paypal_ipn=ipn_obj, user_plan=user_plan, order=order
    )


valid_ipn_received.connect(receive_ipn)
