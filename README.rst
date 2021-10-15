=============================
Django plans paypal
=============================

.. image:: https://badge.fury.io/py/django-plans-paypal.svg
    :target: https://badge.fury.io/py/django-plans-paypal

.. image:: https://travis-ci.org/PetrDlouhy/django-plans-paypal.svg?branch=master
    :target: https://travis-ci.org/PetrDlouhy/django-plans-paypal

.. image:: https://codecov.io/gh/PetrDlouhy/django-plans-paypal/branch/master/graph/badge.svg
    :target: https://codecov.io/gh/PetrDlouhy/django-plans-paypal

Almost automatic integration between `django-plans <https://github.com/django-getpaid/django-plans>`_ and `django-paypal <https://github.com/spookylukey/django-paypal>`_.
This will add subscribe buttons to the order page and automatically confirm the `Order` after the payment.

Currently it is in experimetal stage, wher only recurring payments (subscribtions) are supported.


Documentation
-------------

The full documentation is at https://django-plans-paypal.readthedocs.io.

Quickstart
----------

Install and configure ``django-plans`` and ``django-paypal`` apps.
Capture mode is not yet supported, so ``PAYMENT_VARINANTS`` with ``'capture': False`` will not get confirmed.

Install Django plans paypal::

    pip install django-plans-paypal

Add it to your ``INSTALLED_APPS``, before the ``plans``:

.. code-block:: python

    INSTALLED_APPS = (
        ...
        'payments',
        'paypal.standard.ipn',
        'plans_paypal',
        ...
    )

Add your bussiness account e-mail address to settings:

.. code-block:: python

   PAYPAL_BUSSINESS_EMAIL = "foo@bar.com"

Add Django ``plans_paypal`` to the URL patterns:

.. code-block:: python

    urlpatterns = [
        ...
        url(r'^plans-paypal', include('plans_paypal.urls')),
        ...
    ]

Override `django-plans` class `CreateOrderView` so that `get_success_url()` returns url of `paypal-payment` view:

.. code-block:: python

    def get_success_url(self):
       return reverse("paypal-payment", kwargs={'order_id': self.object.id})

Sandbox testing
---------------

Set follwing settings:

.. code-block:: python

   PAYPAL_TEST_BUSSINESS_EMAIL = "foo@bar.com"
   PAYPAL_TEST = True

Redirect user to `paypal-payment-sandbox` instead of `paypal-payment` view.

Features
--------

* TODO

Running Tests
-------------

Does the code actually work?

::

    source <YOURVIRTUALENV>/bin/activate
    (myenv) $ pip install tox
    (myenv) $ tox

Credits
-------

Tools used in rendering this package:

*  Cookiecutter_
*  `cookiecutter-djangopackage`_

.. _Cookiecutter: https://github.com/audreyr/cookiecutter
.. _`cookiecutter-djangopackage`: https://github.com/pydanny/cookiecutter-djangopackage
