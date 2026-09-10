"""The fan reading must not reach a module either.

test_check_redfish_fan_speed.py covers the inventory item path. These cover the module
backend, which that test cannot reach, and guard the nameplate speeds every other
component reports, which must still be stored.
"""

from module.netbox.object_classes import NBInventoryItem, NBModule


def fan_inventory(reading, reading_unit="RPM"):
    return {"inventory": {"fan": [
        {"name": "System Board Fan1", "id": "0x17||Fan.Embedded.1", "health_status": "OK",
         "operation_status": "Enabled", "physical_context": "SystemBoard",
         "reading": reading, "reading_unit": reading_unit}]}}


def sync(context, reading, reading_unit="RPM"):
    context.source.inventory_file_content = fan_inventory(reading, reading_unit)
    context.source.update_fan()


def only(context, object_type):
    items = context.inventory.get_all_items(object_type)
    assert len(items) == 1
    return items[0]


def test_module_is_not_rewritten_when_the_fan_reading_drifts(check_redfish_source):
    """The regression: a fan at 4200 RPM reading 4320 on the next scan is the same fan."""

    context = check_redfish_source(model_components_as_modules=True)
    sync(context, 4200)
    module = only(context, NBModule)
    module.updated_items = list()

    sync(context, 4320)

    assert module.updated_items == []


def test_fan_health_is_still_synced(check_redfish_source):
    """Dropping the reading must not drop what the fan is actually for."""

    context = check_redfish_source(model_components_as_modules=True)
    sync(context, 4200)

    module = only(context, NBModule)
    assert module.data["custom_fields"]["health"] == "OK"
    assert module.data["custom_fields"].get("inventory_speed") is None


def test_nameplate_speeds_are_still_stored(check_redfish_source):
    """Only the fan reading is live. A DIMM's rated speed must still be recorded."""

    context = check_redfish_source()
    context.source.inventory_file_content = {"inventory": {"memory": [
        {"name": "DIMM A1", "id": "DIMM.Socket.A1", "size_in_mb": 32768, "speed": 3200,
         "manufacturer": "Samsung", "serial": "MEM-AAA", "part_number": "PN-MEM",
         "type": "DDR4", "health_status": "OK", "operation_status": "Enabled"}]}}
    context.source.update_memory()

    item = only(context, NBInventoryItem)
    assert item.data["custom_fields"]["inventory_speed"] == "3200MHz"
