"""Domain exceptions for the OCP DSX-Air CLI."""


class OcpAirError(Exception):
    """Base exception for all ocp-dsx-air failures."""

    pass


class DependencyError(OcpAirError):
    """Raised when required tooling are missing or invalid."""

    pass


class ConfigurationError(OcpAirError):
    """Raised when lab specifications, environment variables, or secrets are missing or invalid."""

    pass


class AirError(OcpAirError):
    """Base exception for NVIDIA Air API and infrastructure failures."""

    pass


class AirSimError(AirError):
    """Raised for Air simulation lifecycle failures (start, stop, checkpoint, lookup)."""

    pass


class AirImageError(AirError):
    """Raised for Air image upload, replacement, or topology alignment failures."""

    pass


class JumpHostError(OcpAirError):
    """Raised when jump host SSH probing, password bootstrapping, or DNS setup fails."""

    pass


class AssistedError(OcpAirError):
    """Raised for Assisted Installer API errors, token refresh failures, or host discovery timeouts."""

    pass


class ClusterInstallError(OcpAirError):
    """Raised when the OpenShift cluster installation times out, stalls, or enters an error state."""

    pass


class ClusterVerificationError(OcpAirError):
    """Raised when post-install verification (nodes, versions, operators, MCPs) fails."""

    pass


class ConsoleError(OcpAirError):
    """Raised for browser discovery, proxy binding, or SOCKS API tunnel lifecycle failures."""

    pass
