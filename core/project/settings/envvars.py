from core.core.utils.settings import get_settings_from_environ
from core.core.utils.update import deep_update

deep_update(globals(), get_settings_from_environ(ENVVAR_SETTINGS_PREFIX))  # type: ignore # noqa: F821
