"""A serialized data structure must never be concatenated into a component name.

check_redfish declares `location` as a str but assigns the structured Redfish Location
object to it, so the collector stores that object's Python repr and the inventory file
carries a string like "{'Oem': {'Dell': ...}}". Concatenating it produces a name that is
meaningless and overruns the 64 characters NetBox stores.

The storage enclosure test runs against a captured inventory (see the fixture README).
The controller and physical drive inputs are synthetic: the hardware that produced the
capture reports `location: null` for both.
"""

import json
from pathlib import Path

import pytest

from module.common.misc import get_name_part_or_none
from module.netbox.object_classes import NBInventoryItem

FIXTURE = Path(__file__).parent / "fixtures" / "check_redfish" / "dell_r740xd_storage_enclosure.json"

# the repr check_redfish stores, verbatim from the captured inventory
STRUCTURED_LOCATION = json.loads(FIXTURE.read_text())["inventory"]["storage_enclosure"][0]["location"]


def names(context):
    return [item.data["name"] for item in context.inventory.get_all_items(NBInventoryItem)]


def test_captured_storage_enclosure_name_has_no_serialized_structure(check_redfish_source):
    """The real object shape, driven through the real update_storage_enclosure()."""

    context = check_redfish_source()
    context.source.inventory_file_content = json.loads(FIXTURE.read_text())
    context.source.update_storage_enclosure()

    assert names(context) == ["BP14G+EXP 0:1"]
    name = names(context)[0]
    assert "Oem" not in name
    assert "{" not in name
    assert len(name) <= 64


def test_storage_controller_name_has_no_serialized_structure(check_redfish_source):
    """Synthetic: the captured hardware reports location null for every controller."""

    context = check_redfish_source()
    context.source.inventory_file_content = {"inventory": {"storage_controller": [
        {"name": "PERC H740P Mini", "model": "PERC H740P Mini", "location": STRUCTURED_LOCATION,
         "manufacturer": "DELL", "serial": None, "firmware": "50.5.1", "health_status": "OK",
         "operation_status": "Enabled"}]}}
    context.source.update_storage_controller()

    assert names(context) == ["PERC H740P Mini"]


def test_physical_drive_name_has_no_serialized_structure(check_redfish_source):
    """Synthetic: the captured hardware reports location null for every drive."""

    context = check_redfish_source()
    context.source.inventory_file_content = {"inventory": {"physical_drive": [
        {"name": "NonRAID Physical Disk 0:1:0", "id": "Disk.Bay.0", "location": STRUCTURED_LOCATION,
         "type": "SSD", "model": "MZ7L3480", "manufacturer": "Samsung", "serial": "DRV-AAA",
         "health_status": "OK", "operation_status": "Enabled"}]}}
    context.source.update_physical_drive()

    stored = names(context)[0]
    assert "Oem" not in stored
    assert "{" not in stored
    assert stored.startswith("NonRAID Physical Disk 0:1:0")


def test_plain_string_location_is_still_used(check_redfish_source):
    """The guard must not drop a location that really is a label."""

    context = check_redfish_source()
    enclosure = json.loads(FIXTURE.read_text())
    enclosure["inventory"]["storage_enclosure"][0]["location"] = "Backplane Front"
    context.source.inventory_file_content = enclosure
    context.source.update_storage_enclosure()

    assert names(context) == ["BP14G+EXP 0:1 Backplane Front"]


@pytest.mark.parametrize("value", [
    STRUCTURED_LOCATION,
    "{'Oem': {'Dell': {}}}",
    "['a', 'b']",
    "{'a': 1}",
    {"Oem": {"Dell": {}}},
    ["a", "b"],
])
def test_serialized_structures_are_rejected(value):
    assert get_name_part_or_none(value) is None


@pytest.mark.parametrize("value, expected", [
    ("Backplane Front", "Backplane Front"),
    ("  Slot 5 ", "Slot 5"),
    ("{unresolved}", "{unresolved}"),      # not a literal, so not a structure
    ("{", "{"),
    ("Bay {3}", "Bay {3}"),
    (5, "5"),
    (0, "0"),
    ("", None),
    (None, None),
])
def test_real_names_are_kept(value, expected):
    assert get_name_part_or_none(value) == expected
