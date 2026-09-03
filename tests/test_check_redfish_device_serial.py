"""The Dell Service Tag can be used as the NetBox device serial instead of the system serial.

Drives the real update_device() and find_device_object() against real NBDevice objects.
"""

import types

from module.common.misc import grab
from module.netbox.inventory import NetBoxInventory
from module.netbox.object_classes import NBDevice
from module.sources.check_redfish.import_inventory import CheckRedfish

SYSTEM_SERIAL = "CNEXAMPLE00001"
SERVICE_TAG = "ABC1234"


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
        overwrite_host_name=False,
        dell_serial_from_service_tag=False,
    )

    source.add_necessary_base_objects()

    device = inventory.add_object(NBDevice, data={"name": "server01"}, source=source)
    source.device_object = device

    return source, inventory, device


def dell_system(system_serial=SYSTEM_SERIAL, service_tag=SERVICE_TAG, with_chassis=True):
    """A Dell system as check_redfish reports it: system.serial is the board PPID,
    the Service Tag is chassis.sku."""

    content = {"inventory": {"system": [
        {"id": "1", "name": "System", "manufacturer": "Dell Inc.", "model": "PowerEdge R650",
         "serial": system_serial, "host_name": "server01",
         "health_status": "OK", "power_state": "On"}]}}
    if with_chassis:
        content["inventory"]["chassis"] = [{"id": "1", "sku": service_tag}]
    return content


def run_update_device(source, content, dell_serial_from_service_tag):
    source.settings.dell_serial_from_service_tag = dell_serial_from_service_tag
    source.inventory_file_content = content
    source.update_device()
    return source.device_object


def match_content(system_serial=SYSTEM_SERIAL, service_tag=SERVICE_TAG):
    """An inventory file without meta.inventory_id, so matching falls back to the serial."""

    return {"inventory": {
        "system": [{"manufacturer": "Dell Inc.", "serial": system_serial}],
        "chassis": [{"sku": service_tag}],
    }}


def test_serial_defaults_to_the_system_serial():
    """Existing behaviour with the option off, which must not change."""

    source, _, _ = make_source()
    device = run_update_device(source, dell_system(), dell_serial_from_service_tag=False)

    assert device.data["serial"] == SYSTEM_SERIAL
    assert grab(device, "data.custom_fields.service_tag") == SERVICE_TAG
    assert grab(device, "data.custom_fields.system_serial") is None


def test_option_makes_the_service_tag_the_serial():
    source, _, _ = make_source()
    device = run_update_device(source, dell_system(), dell_serial_from_service_tag=True)

    assert device.data["serial"] == SERVICE_TAG
    assert grab(device, "data.custom_fields.system_serial") == SYSTEM_SERIAL
    assert grab(device, "data.custom_fields.service_tag") == SERVICE_TAG


def test_option_falls_back_when_no_service_tag_is_reported():
    """No Service Tag means no swap, so the serial is not lost."""

    source, _, _ = make_source()
    device = run_update_device(source, dell_system(with_chassis=False),
                               dell_serial_from_service_tag=True)

    assert device.data["serial"] == SYSTEM_SERIAL
    assert grab(device, "data.custom_fields.system_serial") is None


def test_blank_service_tag_is_not_a_service_tag():
    source, _, _ = make_source()
    device = run_update_device(source, dell_system(service_tag="   "),
                               dell_serial_from_service_tag=True)

    assert grab(device, "data.custom_fields.service_tag") is None
    assert device.data["serial"] == SYSTEM_SERIAL
    assert grab(device, "data.custom_fields.system_serial") is None


def test_system_serial_custom_field_is_not_overwritten_with_none():
    """A transient missing system serial must not clear the field on a later sync."""

    source, _, _ = make_source()
    device = run_update_device(source, dell_system(), dell_serial_from_service_tag=True)
    assert grab(device, "data.custom_fields.system_serial") == SYSTEM_SERIAL

    run_update_device(source, dell_system(system_serial=None), dell_serial_from_service_tag=True)

    assert grab(device, "data.custom_fields.system_serial") == SYSTEM_SERIAL


def test_device_is_matched_by_system_serial():
    """Existing fallback matching, which must not change."""

    source, inventory, _ = make_source()
    source.settings.dell_serial_from_service_tag = False
    existing = inventory.add_object(
        NBDevice, data={"name": "dell-host", "serial": SYSTEM_SERIAL}, source=source)

    source.inventory_file_content = match_content()

    assert source.find_device_object("dell-host.json") is True
    assert source.device_object is existing


def test_device_not_yet_migrated_is_matched_by_system_serial_with_the_option_on():
    source, inventory, _ = make_source()
    source.settings.dell_serial_from_service_tag = True
    existing = inventory.add_object(
        NBDevice, data={"name": "dell-host", "serial": SYSTEM_SERIAL}, source=source)

    source.inventory_file_content = match_content()

    assert source.find_device_object("dell-host.json") is True
    assert source.device_object is existing


def test_device_persisted_with_the_service_tag_is_matched_by_it():
    """Without the Service Tag fallback such a device is skipped and stops being updated."""

    source, inventory, _ = make_source()
    source.settings.dell_serial_from_service_tag = True
    existing = inventory.add_object(
        NBDevice, data={"name": "dell-host", "serial": SERVICE_TAG}, source=source)

    source.inventory_file_content = match_content()

    assert source.find_device_object("dell-host.json") is True
    assert source.device_object is existing


def test_service_tag_matching_does_not_depend_on_the_option():
    """Disabling the option must not strand a device already persisted with the Service Tag."""

    source, inventory, _ = make_source()
    source.settings.dell_serial_from_service_tag = False
    existing = inventory.add_object(
        NBDevice, data={"name": "dell-host", "serial": SERVICE_TAG}, source=source)

    source.inventory_file_content = match_content()

    assert source.find_device_object("dell-host.json") is True
    assert source.device_object is existing


def test_padded_system_serial_still_matches():
    """update_device() stores the serial stripped, so the lookup must strip it too."""

    source, inventory, _ = make_source()
    existing = inventory.add_object(
        NBDevice, data={"name": "dell-host", "serial": SYSTEM_SERIAL}, source=source)

    source.inventory_file_content = match_content(system_serial=f"  {SYSTEM_SERIAL}  ")

    assert source.find_device_object("dell-host.json") is True
    assert source.device_object is existing


def test_missing_serial_does_not_match_a_serial_less_device():
    """get_by_data() matches on exact dict equality, so probing serial=None would match wrongly."""

    source, inventory, _ = make_source()
    inventory.add_object(NBDevice, data={"name": "serial-less"}, source=source)

    source.inventory_file_content = {"inventory": {
        "system": [{"manufacturer": "Dell Inc."}],
    }}

    assert source.find_device_object("dell-host.json") is False


def seed_device_with_nb_id(source, inventory, nb_id, name="wrong-device"):
    device = inventory.add_object(NBDevice, data={"name": name}, source=source)
    device.nb_id = nb_id
    return device


def id_content(inventory_id):
    content = match_content()
    content["meta"] = {"inventory_id": inventory_id}
    return content


def test_integer_inventory_id_is_used():
    """The normal path, which must keep working."""

    source, inventory, _ = make_source()
    wanted = seed_device_with_nb_id(source, inventory, 1, name="by-id")

    source.inventory_file_content = id_content(1)

    assert source.find_device_object("host.json") is True
    assert source.device_object is wanted


def test_digit_string_inventory_id_is_used():
    """meta.inventory_id arrives from JSON, where it may be quoted."""

    source, inventory, _ = make_source()
    wanted = seed_device_with_nb_id(source, inventory, 1, name="by-id")

    source.inventory_file_content = id_content("1")

    assert source.find_device_object("host.json") is True
    assert source.device_object is wanted


def test_boolean_inventory_id_does_not_match_device_one():
    """int(True) is 1, so a JSON `true` would silently claim the device with id 1."""

    source, inventory, _ = make_source()
    seed_device_with_nb_id(source, inventory, 1)
    by_serial = inventory.add_object(
        NBDevice, data={"name": "dell-host", "serial": SYSTEM_SERIAL}, source=source)

    source.inventory_file_content = id_content(True)

    assert source.find_device_object("host.json") is True
    assert source.device_object is by_serial


def test_float_inventory_id_does_not_match_the_truncated_device():
    """int(1.9) is 1, so a JSON float would silently claim the device with id 1."""

    source, inventory, _ = make_source()
    seed_device_with_nb_id(source, inventory, 1)
    by_serial = inventory.add_object(
        NBDevice, data={"name": "dell-host", "serial": SYSTEM_SERIAL}, source=source)

    source.inventory_file_content = id_content(1.9)

    assert source.find_device_object("host.json") is True
    assert source.device_object is by_serial


def test_non_positive_inventory_id_is_rejected():
    """NetBox ids start at 1, so zero and negatives are not usable ids."""

    source, inventory, _ = make_source()
    seed_device_with_nb_id(source, inventory, 0)
    by_serial = inventory.add_object(
        NBDevice, data={"name": "dell-host", "serial": SYSTEM_SERIAL}, source=source)

    source.inventory_file_content = id_content(0)

    assert source.find_device_object("host.json") is True
    assert source.device_object is by_serial
