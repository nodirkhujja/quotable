from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class NoPasswordSignupAdapter(DefaultAccountAdapter):
    """Blocks email/password signup — email users can't create accounts."""

    def is_open_for_signup(self, request):
        return False


class OpenSocialAdapter(DefaultSocialAccountAdapter):
    """Allows signup via Google OAuth. This is the intended path."""

    def is_open_for_signup(self, request, sociallogin):
        return True
