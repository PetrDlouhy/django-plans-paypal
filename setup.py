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


if sys.argv[-1] == "publish":
    try:
        import wheel

        print("Wheel version: ", wheel.__version__)
    except ImportError:
        print('Wheel library missing. Please run "pip install wheel"')
        sys.exit()
    os.system("python setup.py sdist upload")
    os.system("python setup.py bdist_wheel upload")
    sys.exit()

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
    license="MIT",
    zip_safe=False,
    keywords="django-plans-paypal",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Framework :: Django :: 1.11",
        "Framework :: Django :: 2.0",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
    ],
)
