if IN_DOCKER:  # type: ignore # noqa: F821
    assert MIDDLEWARE[:2] == [  # type: ignore # noqa: F821
        "core.project.middleware.RequestIDMiddleware",
        "django.middleware.security.SecurityMiddleware",
    ]
