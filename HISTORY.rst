.. :changelog:

History
-------

0.3.0 (2021-10-15)
++++++++++++++++++
fixes and improvements to the sandbox/production functionality
reverse sandbox logic - if no `PAYPAL_TEST`, the sandbox view would return production so nobody can pay through sandbox on production server

0.2.2 (2021-10-12)
++++++++++++++++++
fix foregotten pudb

0.2.1 (2021-10-12)
++++++++++++++++++
add sandbox view, so both production and sandbox can be used on one server

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
