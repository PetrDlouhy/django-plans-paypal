# -*- coding: utf-8
from __future__ import absolute_import, unicode_literals


DEBUG = True
USE_TZ = True

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "-#5o&sf4iu8&-@na$ad*(t)0gl6_gnw-7_=mk5!zcck)p0w&30"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

ROOT_URLCONF = "tests.urls"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sites",
    "plans",
    "paypal.standard.ipn",
    "plans_paypal",
]

SITE_ID = 1

PAYPAL_BUSSINESS_EMAIL = "fake@email.com"

MIDDLEWARE = ("author.middlewares.AuthorDefaultBackendMiddleware",)

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {
            "debug": True,
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
            "loaders": (
                "admin_tools.template_loaders.Loader",
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ),
        },
    },
]
