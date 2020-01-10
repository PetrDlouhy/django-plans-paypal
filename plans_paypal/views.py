from django.conf import settings
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from paypal.standard.forms import PayPalPaymentsForm

from plans.models import Order


def view_that_asks_for_money(request, order_id):
    order = get_object_or_404(Order, pk=order_id)

    # What you want the button to do.
    paypal_dict = {
        "cmd": "_xclick-subscriptions",
        "business": settings.PAYPAL_BUSSINESS_EMAIL,
        "a3": str(order.total()),                      # monthly price
        "p3": order.pricing.period,                           # duration of each unit (depends on unit)
        "t3": "D",                         # duration unit ("M for Month")
        "src": "1",                        # make payments recur
        "sra": "1",                        # reattempt payment on payment error
        "no_note": "1",                    # remove extra notes (optional)
        "item_name": order.name,
        "notify_url": request.build_absolute_uri(reverse('paypal-ipn')),
        "return": request.build_absolute_uri(reverse('order_payment_success', kwargs={'pk': order_id})),
        "cancel_return": request.build_absolute_uri(reverse('order_payment_failure', kwargs={'pk': order_id})),
        "custom": '{"user_plan_id": %s, "plan_id": %s, "pricing_id": %s, "first_order_id": %s}' % \
        (order.user.userplan.pk, order.plan.pk, order.pricing.pk, order.pk),
    }
    print(paypal_dict)

    # Create the instance.
    form = PayPalPaymentsForm(initial=paypal_dict, button_type="subscribe")
    context = {"form": form}
    return render(request, "paypal_payments/payment.html", context)
