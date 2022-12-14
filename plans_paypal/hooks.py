import ast
import logging

from django.conf import settings
from paypal.standard.ipn.signals import valid_ipn_received
from paypal.standard.models import ST_PP_COMPLETED, ST_PP_PENDING
from plans.models import Order

from .models import PayPalPayment


logger = logging.getLogger(__name__)


def parse_custom(custom):
    custom = custom.replace("null", "None")
    return ast.literal_eval(custom)


def get_order_id(ipn_obj):
    try:
        custom = parse_custom(ipn_obj.custom)
        return custom["first_order_id"]
    except SyntaxError:
        logger.error(
            "Can't parse custom data",
            extra={
                "custom_data": ipn_obj.custom,
                "ipn_obj": ipn_obj,
            },
        )
        return ipn_obj.item_number


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

    order = Order.objects.get(pk=get_order_id(ipn_obj))
    print("Order: ", order.id)
    user_plan = order.user.userplan

    if ipn_obj.is_subscription_cancellation():
        if user_plan and hasattr(user_plan, "recurring"):
            if str(user_plan.recurring.token) == str(ipn_obj.subscr_id):
                user_plan.recurring.delete()
            else:
                logger.warning(
                    "Recurring user plan not found by ID, can't cancell subscription",
                    extra={
                        "recurring_token": user_plan.recurring.token,
                        "ipn_token": ipn_obj.subscr_id,
                        "recurring": user_plan.recurring,
                        "ipn_obj": ipn_obj,
                    },
                )
        return None
    elif ipn_obj.is_subscription_payment() and ipn_obj.payment_status == ST_PP_PENDING:
        # Pending status
        return None
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
        if order.status != Order.STATUS.NEW:
            order = Order.objects.create(
                user=user_plan.user,
                plan=user_plan.plan,
                pricing=order.pricing,
                amount=ipn_obj.mc_gross,
                tax=order.tax,
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
    logger.error(
        "IPN with unknown status",
        extra={
            "ipn_obj": ipn_obj,
            "ipn_status": ipn_obj.payment_status,
        },
    )


valid_ipn_received.connect(receive_ipn)
