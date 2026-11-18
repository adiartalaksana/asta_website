import os

from .base import *

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
ALLOWED_HOSTS = os.environ["DJANGO_ALLOWED_HOSTS"].split(",")
DATABASES["default"].update(
    NAME=os.environ["POSTGRES_DB"],
    USER=os.environ["POSTGRES_USER"],
    PASSWORD=os.environ["POSTGRES_PASSWORD"],
    HOST=os.environ["POSTGRES_HOST"],
)
