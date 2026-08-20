#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import re
import sys

from setuptools import find_packages

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup


def get_version(*file_paths):
    """Retrieves the version from plans_paypal/__init__.py"""
    filename = os.path.join(os.path.dirname(__file__), *file_paths)
    version_file = open(filename).read()
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")


def parse_requirements(file_name):
    requirements = []
    for line in open(file_name, "r").read().split("\n"):
        if re.match(r"(\s*#)|(\s*$)", line):
            continue
        if re.match(r"\s*-e\s+", line):
            requirements.append(re.sub(r"\s*-e\s+.*#egg=(.*)$", r"\1", line))
        elif re.match(r"(\s*git)|(\s*hg)", line):
            pass
        else:
            requirements.append(line)
    return requirements


version = get_version("plans_paypal", "__init__.py")


if sys.argv[-1] == "tag":
    print("Tagging the version on git:")
    os.system("git tag -a %s -m 'version %s'" % (version, version))
    os.system("git push --tags")
    sys.exit()

readme = open("README.rst").read()
history = open("HISTORY.rst").read().replace(".. :changelog:", "")

setup(
    name="django-plans-paypal",
    version=version,
    description="""Integration between django-plans and django-paypal.""",
    long_description=readme + "\n\n" + history,
    author="Petr Dlouhý",
    author_email="petr.dlouhy@email.cz",
    url="https://github.com/PetrDlouhy/django-plans-paypal",
    packages=find_packages(
        exclude=[
            "tests.templates",
            "plans_paypal.templates.paypal_payments",
            "plans_paypal.templates",
        ]
    ),
    include_package_data=True,
    install_requires=parse_requirements("requirements.txt"),
    python_requires=">=3.9",
    license="MIT",
    zip_safe=False,
    keywords="django-plans-paypal",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Framework :: Django :: 4.2",
        "Framework :: Django :: 5.0",
        "Framework :: Django :: 5.1",
        "Framework :: Django :: 5.2",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
)
