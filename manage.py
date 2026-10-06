#!/usr/bin/env python
import os, sys

# Management commands default to local development. WSGI defaults to production.
os.environ.setdefault("DEBUG", "1")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.core.management import execute_from_command_line

execute_from_command_line(sys.argv)
