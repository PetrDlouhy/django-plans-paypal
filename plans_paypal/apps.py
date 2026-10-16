from django.apps import AppConfig


class PaypalPaymentsAppConfig(AppConfig):
    name = "plans_paypal"
    default_auto_field = "django.db.models.AutoField"

    def ready(self):
        from . import hooks  # noqa
