"""An interface the source discovered no IPs for must keep the IPs it already has.

Drives the real add_update_interface() IP removal loop against real NBInterface and
NBIPAddress objects.
"""

import types

from module.netbox.inventory import NetBoxInventory
from module.netbox.object_classes import NBDevice, NBInterface, NBIPAddress
from module.sources.check_redfish.import_inventory import CheckRedfish


def make_source():
    """Build a CheckRedfish source on a fresh inventory, with the real base objects registered."""

    inventory = NetBoxInventory()
    # reset the singleton state so each test starts from an empty inventory
    inventory.init()
    inventory.source_list = list()
    inventory.netbox_api_version = "4.3.0"

    source = object.__new__(CheckRedfish)
    source.inventory = inventory
    source.name = "test"
    source.source_tag = "Source: test"
    source.settings = types.SimpleNamespace(
        ip_tenant_inheritance_order=list(),
        permitted_subnets=None,
        skip_fhrp_group_ips=False,
    )

    source.add_necessary_base_objects()

    device = inventory.add_object(NBDevice, data={"name": "server01"}, source=source)
    source.device_object = device

    return source, inventory, device


def seed_interface_with_ip(source, inventory, device, name="pnet0", address="172.10.10.12/24"):
    interface = inventory.add_object(NBInterface, data={"name": name, "device": device}, source=source)
    ip = inventory.add_object(
        NBIPAddress, data={"address": address, "assigned_object_id": interface}, source=source)
    assert ip in interface.get_ip_addresses()
    return interface, ip


def test_ip_is_kept_when_the_source_discovered_no_ips():
    """The management IP on a bond or bridge matched only by a shared MAC must survive a sync."""

    source, inventory, device = make_source()
    interface, ip = seed_interface_with_ip(source, inventory, device)

    source.add_update_interface(interface, device, {"name": "pnet0"}, [], keep_undiscovered_ips=True)

    # unset_attribute() queues the de-assignment in unset_items, it does not mutate data
    assert "assigned_object_id" not in ip.unset_items


def test_ip_is_still_removed_by_default():
    """Other sources are unchanged: an IP no longer reported is still removed."""

    source, inventory, device = make_source()
    interface, ip = seed_interface_with_ip(source, inventory, device)

    source.add_update_interface(interface, device, {"name": "pnet0"}, [])

    assert "assigned_object_id" in ip.unset_items


def test_ip_is_still_removed_when_other_ips_are_discovered():
    """The guard covers an empty discovery only. An IP dropped from a non-empty set still goes."""

    source, inventory, device = make_source()
    interface, ip = seed_interface_with_ip(source, inventory, device)

    source.add_update_interface(interface, device, {"name": "pnet0"}, ["198.51.100.7/24"],
                                keep_undiscovered_ips=True)

    assert "assigned_object_id" in ip.unset_items


def test_ip_is_still_removed_when_the_discovered_ips_are_unusable():
    """A non-empty discovery is a statement about the interface even when none of the addresses
    survive parsing, so the guard must not treat it as "discovered nothing"."""

    source, inventory, device = make_source()
    interface, ip = seed_interface_with_ip(source, inventory, device)

    source.add_update_interface(interface, device, {"name": "pnet0"}, ["not-an-ip"],
                                keep_undiscovered_ips=True)

    assert "assigned_object_id" in ip.unset_items
