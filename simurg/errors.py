"""Errors for simurg."""


class SimurgError(Exception):
    pass


class ConfigError(SimurgError):
    pass


class ScrapeError(SimurgError):
    def __init__(self, message, payload=None):
        self.payload = payload
        super().__init__(message)


class UploadError(SimurgError):
    pass


class RequestError(SimurgError):
    pass


class RequestFailedError(RequestError):
    pass


class LoginError(RequestError):
    pass


class ImageUploadFailed(SimurgError):
    pass


class AbortAndDeleteFolder(SimurgError):
    pass
