from collections.abc import Mapping
from typing import Final

from ocp_dsx_air.core.contracts import IssueCode

REMEDIATION_HINTS: Final[Mapping[IssueCode, str]] = {
    IssueCode.HOST_INSTALLING_PENDING_USER_ACTION: (
        "The host has not returned after the installer reboot. With the permanent "
        "['hd', 'cdrom'] boot order, a bootable installed disk should win automatically. "
        "The deployment will verify that the simulation is active, cpu_mode is "
        "host-passthrough, the OOB address is reachable, and cluster DNS resolves from "
        "the jump host; it will not detach the CD-ROM or switch to HD-only boot."
    ),
    IssueCode.HOST_RESETTING_PENDING_USER_ACTION: (
        "The host must return to the discovery environment to finish the Assisted "
        "Installer reset. The supported configuration is boot ['hd', 'cdrom'] with the "
        "current infraenv-derived discovery image attached. The deployment may reattach "
        "the image or rebuild a node only when remote state proves installation never "
        "started; otherwise it stops without wiping the disk."
    ),
    IssueCode.NO_OOB_AFTER_REBOOT: (
        "The host's OOB address is still unreachable after the installer reboot. The "
        "deployment will retry the probe and verify simulation state, node boot health, "
        "DHCP connectivity, and cpu_mode=host-passthrough without changing the permanent "
        "['hd', 'cdrom'] boot order."
    ),
    IssueCode.WRONG_DISCOVERY_BOOT: (
        "The Air node does not match the supported discovery configuration: boot order "
        "must remain ['hd', 'cdrom'] and the CD-ROM must reference the discovery image "
        "derived from the current infraenv. The deployment will repair this only while "
        "the node is known to be pre-installation."
    ),
    IssueCode.HOST_INSUFFICIENT: (
        "Host validations are failing (often NTP or majority-connectivity on Air). "
        "Wait — NTP is not a fail-fast; discovery continues until known/ready or timeout."
    ),
}
