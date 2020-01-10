import json

from django.conf import settings
from paypal.standard.ipn.signals import valid_ipn_received
from paypal.standard.models import ST_PP_COMPLETED
from plans.models import UserPlan, Order, Pricing


def show_me_the_money(sender, **kwargs):
    print("paypal hook")
    ipn_obj = sender
    print(ipn_obj.payment_status)
    print(ipn_obj.receiver_email)
    if ipn_obj.payment_status == ST_PP_COMPLETED:
        # WARNING !
        # Check that the receiver email is the same we previously
        # set on the `business` field. (The user could tamper with
        # that fields on the payment form before it goes to PayPal)
        print(ipn_obj.receiver_email)
        if ipn_obj.receiver_email != settings.PAYPAL_BUSSINESS_EMAIL:
            # Not a valid payment
            return

        # ALSO: for the same reason, you need to check the amount
        # received, `custom` etc. are all what you expect or what
        # is allowed.

        # Undertake some action depending upon `ipn_obj`.
        print(ipn_obj.custom)
        custom = json.loads(ipn_obj.custom)
        print(custom)
        user_plan = UserPlan.objects.get(pk=custom['user_plan_id'])
        pricing = Pricing.objects.get(pk=custom['pricing_id'])
        order = Order.objects.get(pk=custom['first_order_id'])
        if order.status == 2:
            order = Order.objects.create(user=user_plan.user, plan=user_plan.plan, pricing=pricing, amount=ipn_obj.mc_gross, currency=ipn_obj.mc_currency)
        order.complete_order()


valid_ipn_received.connect(show_me_the_money)
