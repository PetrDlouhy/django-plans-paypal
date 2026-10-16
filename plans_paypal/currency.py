def paypal_currency(order):
    """Currency of ``order``'s subscription at PayPal.

    The subscribe button sends it as ``currency_code``, and PayPal cannot
    change the currency of an existing subscription, so every payment of
    the subscription is charged in it.
    """
    return order.currency.upper()
