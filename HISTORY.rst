.. :changelog:

History
-------

unreleased
++++++++++

* renewal orders recalculate the tax rate for the user's current billing
  data instead of copying it from the subscription's first order forever.
  PayPal charges a fixed gross, so the new rate is applied top-down: the
  gross stays, the net amount adjusts (at most one cent off when no
  cent-exact net exists for the new rate). Without ``PLANS_TAXATION_POLICY``,
  without ``BillingInfo``, or on a failed VIES/rate lookup the previous
  behaviour (copied tax) is kept.

1.3.0 (2026-08-19)
++++++++++++++++++

* ``receive_ipn`` now processes each IPN in a single database
  transaction: a failure while completing the order no longer leaves an
  armed ``RecurringUserPlan`` and a recorded payment behind, so PayPal's
  IPN retries start from a clean slate instead of dying on the leftover
  state as duplicates.
* pending subscription payments (typically eChecks clearing) are
  recorded as ``PayPalPayment`` rows instead of being silently dropped
  -- an in-flight payment is not a decline.

1.2.0 (2026-05-20)
++++++++++++++++++

* ``PaymentFailureView``: redirect already-completed orders to the success
  page (with a warning log) instead of raising ``ValueError``. PayPal can
  send the buyer to the ``cancel_return`` URL even after the IPN has
  marked the order as completed (browser back button, stale tabs, edge
  cases in the PayPal flow); the previous behaviour produced unhandled
  500s for paid users. Reverses the 0.7.1 behaviour change.
* handle empty/whitespace PayPal IPN ``custom`` data without logging an
  error; also catch ``ValueError`` from ``ast.literal_eval`` so future
  Python releases can't break the parser
* admin: add ``paypal_ipn__txn_id`` column to ``PayPalPaymentAdmin.list_display``
* drop conflicting isort ``lines_after_imports = 2`` setting (project
  already uses ``profile = black``) and reformat affected files; the
  ``lint`` CI job is green again

1.1.0 (2025-06-20)
++++++++++++++++++

* add more fields to admin list_display

1.0.1 (2025-05-29)
++++++++++++++++++

* fixes to taking the data from custom_data

1.0.0 (2025-05-23)
++++++++++++++++++

* create order more robust: take the data from custom_data
* Support Django up to 5.2

0.7.1 (2024-04-23)
++++++++++++++++++
* fail if PaymentFailureView is requested on completed orders

0.7.0 (2024-04-12)
++++++++++++++++++
* migrate code to RecurringUserPlan.renewal_triggered_by
* migrate data of RecurringUserPlans with payment_provider="paypal-recurring" and renewal_triggered_by=TASK to renewal_triggered_by=OTHER

0.6.0 (2023-03-22)
+++++++++++++++++++
* Fix amount received on recurring payments, add tests

0.5.1 (2023-02-11)
+++++++++++++++++++
* allow only logged users to access the failure view

0.5.0 (2022-12-14)
+++++++++++++++++++

* Fix tax received on recurring payments
* More robust receiving original Order ID (if PayPal fails to handle custom_data)
* update to Django 4.1
* fix completing recurring payments if first order status is returned
* only log error if custom data can't be parsed

0.4.10 (2022-04-29)
+++++++++++++++++++
* fix problem with duplicate payments
* PayPalPaymentAdmin: display connected IPN fields

0.4.9 (2022-03-07)
++++++++++++++++++
* make parsing custom_data more robust

0.4.8 (2022-01-19)
++++++++++++++++++
* fix last release
* add tests and test on all supported Django/Python versions through GitHub actions

0.4.7 (2022-01-13)
++++++++++++++++++
* fix problem if there was ' in custom payment data

0.4.6 (2021-12-02)
++++++++++++++++++
* fix problem with creating bad JSON

0.4.5 (2021-12-01)
++++++++++++++++++
* fix IPN field editing in PayPalPaymentAdmin
* store also user e-mail in custom to enable search in IPN admin

0.4.4 (2021-12-01)
++++++++++++++++++
* create PayPalPayment before completing the order

0.4.3 (2021-11-30)
++++++++++++++++++
* fix problem with recurring payments
* add created/modified/author auto fields

0.4.2 (2021-11-08)
++++++++++++++++++
* fix problem with other IPN types

0.4.1 (2021-10-18)
++++++++++++++++++
* cancel order after returning to failure URL

0.4.0 (2021-10-18)
++++++++++++++++++
* allow to set up encrypted PayPal form

0.3.0 (2021-10-15)
++++++++++++++++++
* fixes and improvements to the sandbox/production functionality
* reverse sandbox logic - if no `PAYPAL_TEST`, the sandbox view would return production so nobody can pay through sandbox on production server

0.2.2 (2021-10-12)
++++++++++++++++++
* fix foregotten pudb

0.2.1 (2021-10-12)
++++++++++++++++++
* add sandbox view, so both production and sandbox can be used on one server

0.2.0 (2021-10-11)
++++++++++++++++++
* fix periods to complain with PayPal maximal durations

0.1.0 (2021-10-08)
++++++++++++++++++
* hook ipn.PayPalIpn object with plans.Order (for later usage e.g. determining PayPal fee)
* set plan renewal for django-plans

0.0.2 (2018-08-05)
++++++++++++++++++

* Payment process without capturing should work
* Automatic buttons generation

0.0.1 (2018-07-23)
++++++++++++++++++

* First release on PyPI.
