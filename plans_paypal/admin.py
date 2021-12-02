from django.contrib import admin

from .models import PayPalPayment


@admin.register(PayPalPayment)
class PayPalPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "user_plan",
        "paypal_ipn",
        "created",
    )
    autocomplete_fields = (
        "order",
        "user_plan",
    )
    raw_id_fields = ("paypal_ipn",)
    search_fields = ("user_plan__user__email",)
    readonly_fields = (
        "author",
        "created",
        "modified",
        "updated_by",
    )
