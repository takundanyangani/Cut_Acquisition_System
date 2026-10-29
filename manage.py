#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def main():
    default = "config.settings_test" if sys.argv[1:2] == ["test"] else "config.settings"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", default)
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
