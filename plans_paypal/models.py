from django.db import models


class PayPalPayment(models.Model):
    order = models.ForeignKey(
        'plans.Order',
        on_delete=models.CASCADE,
        null=False,
        blank=False,
    )
    user_plan = models.ForeignKey(
        'plans.UserPlan',
        on_delete=models.CASCADE,
        null=False,
        blank=False,
    )
    paypal_ipn = models.OneToOneField(
        'ipn.PayPalIpn',
        on_delete=models.CASCADE,
        null=False,
        blank=False,
    )
