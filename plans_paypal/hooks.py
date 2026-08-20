import ast
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from paypal.standard.ipn.signals import valid_ipn_received
from paypal.standard.models import ST_PP_COMPLETED, ST_PP_PENDING
from plans import utils as plans_utils
from plans.base.models import AbstractBillingInfo, AbstractRecurringUserPlan
from plans.models import Order, Plan, Pricing

from .models import PayPalPayment

logger = logging.getLogger(__name__)


def parse_custom(custom):
    custom = custom.replace("null", "None")
    return ast.literal_eval(custom)


def get_custom_data(ipn_obj):
    if not ipn_obj.custom or not ipn_obj.custom.strip():
        return {"first_order_id": ipn_obj.item_number}
    try:
        return parse_custom(ipn_obj.custom)
    except (SyntaxError, ValueError):
        logger.exception(
            "Can't parse custom data",
            extra={
                "custom_data": ipn_obj.custom,
                "ipn_obj": ipn_obj,
            },
        )
        return {"first_order_id": ipn_obj.item_number}


def _net_amount_for_gross(gross, tax):
    """Net amount on the cent grid whose Order.total() is closest to gross.

    Order.total() derives the gross from net and rate, so for some
    gross/rate pairs no cent-exact net exists; the closest candidate is
    at most one cent off. Prefers the exact match, then the lower total,
    so the order never exceeds what was actually received.
    """
    base = (gross * 100 / (Decimal(tax) + 100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    candidates = []
    for amount in (base - Decimal("0.01"), base, base + Decimal("0.01")):
        total = (amount * (Decimal(tax) + 100) / 100).quantize(Decimal("1.00"))
        candidates.append((abs(total - gross), total, amount))
    difference, total, amount = min(candidates)
    if difference:
        logger.warning(
            "No cent-exact net for gross %s at tax %s, using %s (total %s)",
            gross,
            tax,
            amount,
            total,
        )
    return amount


def get_renewal_tax_and_amount(user, gross):
    """Current tax for the user's billing data, net derived from the fixed gross.

    PayPal charges a fixed gross per subscription, so a tax-rate change
    since the first order has to be applied top-down: the gross stays,
    the net adjusts. Returns None when the current rate cannot be
    determined (no taxation policy, no billing info, VIES failure) -
    the caller then keeps the values copied from the first order.
    """
    if getattr(settings, "PLANS_TAXATION_POLICY", None) is None:
        return None
    if not hasattr(user, "billinginfo"):
        return None
    billing_info = user.billinginfo
    if not billing_info.country:
        return None
    country = billing_info.country.code
    if billing_info.tax_number:
        tax_number = AbstractBillingInfo.get_full_tax_number(
            billing_info.tax_number, country
        )
    else:
        tax_number = None
    tax, request_successful = plans_utils.get_tax_rate(country, tax_number)
    if not request_successful:
        return None
    gross = Decimal(str(gross))
    if tax is None:
        return None, gross
    return tax, _net_amount_for_gross(gross, tax)


def create_new_order(order, user_plan, ipn_obj, custom_ipn_data):
    """
    Create order for automatic plan renewal (create new order)

    This could also happen if the original order was canceled.
    In that case we want to create new order.
    """
    try:
        plan = Plan.objects.get(pk=custom_ipn_data["plan_id"])
        pricing = Pricing.objects.get(pk=custom_ipn_data["pricing_id"])
    except (Plan.DoesNotExist, KeyError):
        plan = user_plan.plan
        pricing = order.pricing
        logger.exception(
            "Plan or pricing not found in custom data",
            extra={
                "custom_data": custom_ipn_data,
                "ipn_obj": ipn_obj,
            },
        )

    # The first order's tax froze at subscription start; recalculate for
    # the current billing data when possible, keeping the charged gross.
    renewal_tax_and_amount = get_renewal_tax_and_amount(
        user_plan.user, ipn_obj.mc_gross
    )
    if renewal_tax_and_amount is None:
        tax, amount = order.tax, order.amount
    else:
        tax, amount = renewal_tax_and_amount

    return Order.objects.create(
        user=user_plan.user,
        plan=plan,
        pricing=pricing,
        amount=amount,
        tax=tax,
        currency=ipn_obj.mc_currency,
    )


@transaction.atomic
def receive_ipn(sender, **kwargs):
    # Atomic, because the completed branch arms the plan renewal and
    # records the PayPalPayment BEFORE completing the order. Without a
    # transaction, a failure inside complete_order() left those earlier
    # writes committed: an armed RecurringUserPlan, an uncompleted order,
    # and PayPal retrying the IPN forever as a duplicate txn_id.
    ipn_obj = sender

    custom_ipn_data = get_custom_data(ipn_obj)
    first_order_id = custom_ipn_data["first_order_id"]

    logger.debug(
        "PayPal IPN received: txn_type=%s payment_status=%s first_order_id=%s",
        ipn_obj.txn_type,
        ipn_obj.payment_status,
        first_order_id,
    )

    if not (
        ipn_obj.is_subscription_cancellation() or ipn_obj.is_subscription_payment()
    ):
        # Not a subscription
        return None

    order = Order.objects.get(pk=first_order_id)
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
        # A pending subscription payment (typically an eCheck clearing) is
        # a payment in flight, not a failure. Dropping it made the attempt
        # invisible -- support would read the subscription as declined and
        # tell the customer to re-subscribe, and PayPal then billed both
        # subscriptions. Record the attempt; the follow-up COMPLETED IPN
        # for the same transaction completes the order as usual.
        logger.info(
            "Pending subscription payment (e.g. eCheck) recorded for order %s",
            order.pk,
            extra={"ipn_obj": ipn_obj},
        )
        return PayPalPayment.objects.create(
            paypal_ipn=ipn_obj, user_plan=user_plan, order=order
        )
    elif (
        ipn_obj.is_subscription_payment() and ipn_obj.payment_status == ST_PP_COMPLETED
    ):
        # WARNING !
        # Check that the receiver email is the same we previously
        # set on the `business` field. (The user could tamper with
        # that fields on the payment form before it goes to PayPal)
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
