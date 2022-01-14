from author.decorators import with_author
from django.db import models
from django_extensions.db.models import TimeStampedModel


@with_author
class PayPalPayment(TimeStampedModel, models.Model):
    order = models.ForeignKey(
        "plans.Order",
        on_delete=models.CASCADE,
        null=False,
        blank=False,
    )
    user_plan = models.ForeignKey(
        "plans.UserPlan",
        on_delete=models.CASCADE,
        null=False,
        blank=False,
    )
    paypal_ipn = models.OneToOneField(
        "ipn.PayPalIPN",
        on_delete=models.CASCADE,
        null=False,
        blank=False,
    )
