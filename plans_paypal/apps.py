from django.apps import AppConfig


class PaypalPaymentsAppConfig(AppConfig):
    name = 'plans_paypal'

    def ready(self):
        from . import hooks  # noqa
