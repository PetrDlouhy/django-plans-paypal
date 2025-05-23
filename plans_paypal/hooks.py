import ast
import logging

from django.conf import settings
from paypal.standard.ipn.signals import valid_ipn_received
from paypal.standard.models import ST_PP_COMPLETED, ST_PP_PENDING
from plans.base.models import AbstractRecurringUserPlan
from plans.models import Order, Plan, Pricing

from .models import PayPalPayment


logger = logging.getLogger(__name__)


def parse_custom(custom):
    custom = custom.replace("null", "None")
    return ast.literal_eval(custom)


def get_custom_data(ipn_obj):
    try:
        return parse_custom(ipn_obj.custom)
    except SyntaxError:
        logger.exception(
            "Can't parse custom data",
            extra={
                "custom_data": ipn_obj.custom,
                "ipn_obj": ipn_obj,
            },
        )
        return {"first_order_id": ipn_obj.item_number}


def create_new_order(order, user_plan, ipn_obj, custom_ipn_data):
    """
    Create order for automatic plan renewal (create new order)

    This could also happen if the original order was canceled.
    In that case we want to create new order.
    """
    try:
        plan = Plan.objects.get(pk=custom_ipn_data["plan_id"])
        pricing = Pricing.objects.get(pk=custom_ipn_data["pricing_id"])
    except (Plan.DoesNotExist, AttributeError):
        plan = user_plan.plan
        pricing = user_plan.pricing
        logger.exception(
            "Plan or pricing not found in custom data",
            extra={
                "custom_data": custom_ipn_data,
                "ipn_obj": ipn_obj,
            },
        )

    return Order.objects.create(
        user=user_plan.user,
        plan=plan,
        pricing=pricing,
        amount=order.amount,
        tax=order.tax,
        currency=ipn_obj.mc_currency,
    )


def receive_ipn(sender, **kwargs):
    print("paypal hook")
    ipn_obj = sender

    custom_ipn_data = get_custom_data(ipn_obj)
    first_order_id = custom_ipn_data["first_order_id"]

    print("Payment status: ", ipn_obj.payment_status)
    print(ipn_obj.receiver_email)
    print(ipn_obj.txn_type)

    if not (
        ipn_obj.is_subscription_cancellation() or ipn_obj.is_subscription_payment()
    ):
        # Not a subscription
        return None

    order = Order.objects.get(pk=first_order_id)
    print("Order: ", order.id)
    user_plan = order.user.userplan

    if ipn_obj.is_subscription_cancellation():
        if user_plan and hasattr(user_plan, "recurring"):
            if str(user_plan.recurring.token) == str(ipn_obj.subscr_id):
                user_plan.recurring.delete()
            else:
                logger.error(
                    "Recurring user plan not found by ID, can't cancel subscription",
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
        if order.total() != ipn_obj.mc_gross:
            logger.error(
                "Received amount doesn't match",
                extra={
                    "order_total": order.total,
                    "ipn_amount": ipn_obj.mc_gross,
                    "ipn_obj": ipn_obj,
                },
            )
            raise Exception("Received amount doesn't match")

        # Undertake some action depending upon `ipn_obj`.
        if order.status != Order.STATUS.NEW:
            order = create_new_order(order, user_plan, ipn_obj, custom_ipn_data)
        user_plan.set_plan_renewal(
            order,
            token=ipn_obj.subscr_id,
            payment_provider="paypal-recurring"
            + ("-sandbox" if ipn_obj.test_ipn else ""),
            renewal_triggered_by=AbstractRecurringUserPlan.RENEWAL_TRIGGERED_BY.OTHER,
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
