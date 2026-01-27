if IN_DOCKER:  # type: ignore # noqa: F821
    assert MIDDLEWARE[:1] == ["django.middleware.security.SecurityMiddleware"]  # type: ignore # noqa: F821
