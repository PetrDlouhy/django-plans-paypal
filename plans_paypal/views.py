import json

from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from paypal.standard.forms import PayPalEncryptedPaymentsForm, PayPalPaymentsForm
from plans.models import Order


class PlansPayPalPaymentsFormMixin:
    def __init__(self, test_mode_enabled=False, *args, **kwargs):
        self.test_mode_enabled = test_mode_enabled
        super().__init__(*args, **kwargs)

    def test_mode(self):
        if self.test_mode_enabled:
            return super().test_mode()
        return False


class PlansPayPalPaymentsForm(PlansPayPalPaymentsFormMixin, PayPalPaymentsForm):
    pass


class PlansPayPalEncryptedPaymentsForm(
    PlansPayPalPaymentsFormMixin, PayPalEncryptedPaymentsForm
):
    pass


def view_that_asks_for_money(request, order_id, sandbox=False):
    order = get_object_or_404(Order, pk=order_id)

    period = order.pricing.period
    duration_unit = "D"

    if 364 > period > 90:  # PayPal accepts max 90 days
        duration_unit = "W"
        period /= 7

    # Dividing into months isn't very precise, so we don't do it
    # if period > 52:  # PayPal accepts max 52 weeks
    #     duration_unit = "M"
    #     period = order.pricing.period / 30

    if period > 364:  # PayPal accepts max 24 months
        duration_unit = "Y"
        period = round((order.pricing.period - 1) / 365, 3)
        # PayPal has to renew the payment sooner than the plan would expire even on leap years.

    # What you want the button to do.
    paypal_dict = {
        "cmd": "_xclick-subscriptions",
        "business": (
            settings.PAYPAL_TEST_BUSSINESS_EMAIL
            if sandbox
            else settings.PAYPAL_BUSSINESS_EMAIL
        ),
        "a3": str(order.total()),  # monthly price
        "p3": period,  # duration of each unit (depends on unit)
        "t3": duration_unit,  # duration unit ("M for Month")
        "src": "1",  # make payments recur
        "sra": "1",  # reattempt payment on payment error
        "no_note": "1",  # remove extra notes (optional)
        "item_name": order.name,
        "notify_url": request.build_absolute_uri(reverse("paypal-ipn")),
        "return": request.build_absolute_uri(
            reverse("order_payment_success", kwargs={"pk": order_id})
        ),
        "cancel_return": request.build_absolute_uri(
            reverse("paypal-payment-failure", kwargs={"order_id": order_id}),
        ),
        "custom": json.dumps(
            {
                "user_plan_id": order.user.userplan.pk,
                "plan_id": order.plan.pk,
                "pricing_id": order.pricing.pk,
                "first_order_id": order.pk,
                "user_email": order.user.email,
            },
        ),
    }
    print(paypal_dict)

    # Create the instance.
    form_kwargs = {}
    if getattr(settings, "PAYPAL_ENCRYPTED_FORM", False):
        Form = PlansPayPalEncryptedPaymentsForm
        if sandbox:
            form_kwargs = {
                "private_cert": settings.PAYPAL_TEST_PRIVATE_CERT,
                "public_cert": settings.PAYPAL_TEST_PUBLIC_CERT,
                "paypal_cert": settings.PAYPAL_TEST_CERT,
                "cert_id": settings.PAYPAL_TEST_CERT_ID,
            }
    else:
        Form = PlansPayPalPaymentsForm
    form = Form(
        initial=paypal_dict,
        button_type="subscribe",
        test_mode_enabled=sandbox,
        **form_kwargs
    )
    context = {"form": form}
    return render(request, "paypal_payments/payment.html", context)


def payment_failure(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    order.status = Order.STATUS.CANCELED
    order.save()
    return redirect(reverse("order_payment_failure", kwargs={"pk": order_id}))
