# https://developer.paypal.com/reference/currency-codes/
ZERO_DECIMAL_CURRENCIES = {"HUF", "JPY", "TWD"}


def paypal_currency(order):
    """Currency of ``order``'s subscription at PayPal.

    The subscribe button sends it as ``currency_code``, and PayPal cannot
    change the currency of an existing subscription, so every payment of
    the subscription is charged in it.
    """
    return order.currency.upper()


def paypal_amount(order):
    """``order``'s total as PayPal accepts it in the order's currency.

    PayPal rejects any decimals, even ".00", for HUF, JPY and TWD.
    """
    total = order.total()
    currency = paypal_currency(order)
    if currency not in ZERO_DECIMAL_CURRENCIES:
        return str(total)
    if total != total.to_integral_value():
        raise ValueError(
            f"{order} totals {total} {currency}: PayPal charges {currency} "
            "in whole units only"
        )
    return str(total.to_integral_value())
