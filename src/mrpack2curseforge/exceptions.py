class Mrpack2CurseForgeError(Exception):
    """Exceção base do projeto."""


class InvalidMrpackError(Mrpack2CurseForgeError):
    pass


class DownloadError(Mrpack2CurseForgeError):
    pass


class ApiError(Mrpack2CurseForgeError):
    pass


class ConversionCancelled(Mrpack2CurseForgeError):
    """O usuário cancelou a conversão."""
