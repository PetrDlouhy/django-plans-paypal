=====
Usage
=====

To use Django plans paypal in a project, add it to your `INSTALLED_APPS`:

.. code-block:: python

    INSTALLED_APPS = (
        ...
        'plans_paypal.apps.PlansPaypalConfig',
        ...
    )

Add Django plans paypal's URL patterns:

.. code-block:: python

    from plans_paypal import urls as plans_paypal_urls


    urlpatterns = [
        ...
        url(r'^', include(plans_paypal_urls)),
        ...
    ]
