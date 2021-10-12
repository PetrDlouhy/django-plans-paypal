from django.urls import include, path

from . import views


urlpatterns = [
    path('paypal-payment/<int:order_id>/', views.view_that_asks_for_money, name='paypal-payment'),
    path(
        'paypal-payment/<int:order_id>/sandbox',
        views.view_that_asks_for_money,
        name='paypal-payment-sandbox',
        kwargs={'sandbox': True},
    ),
    path('paypal/', include('paypal.standard.ipn.urls')),
]
