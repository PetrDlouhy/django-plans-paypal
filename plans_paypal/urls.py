from django.urls import path

from . import views


urlpatterns = [
    path('paypal-payment/<int:order_id>/', views.view_that_asks_for_money, name='paypal-payment'),
]
