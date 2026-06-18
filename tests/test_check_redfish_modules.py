# -*- coding: utf-8 -*-
"""
Integration tests for modeling check_redfish hardware components as NetBox modules
(the modern replacement for the deprecated inventory items).

These tests exercise the real NetBoxInventory and the real NetBoxObject classes through the
actual CheckRedfish source methods - no mocks. The only boundary not exercised is the NetBox
REST API itself (which requires a live NetBox instance, see the plan's verification section).
"""

import types

import pytest

from module.common.misc import grab
from module.netbox.inventory import NetBoxInventory
from module.netbox.object_classes import (
    NBDevice,
    NBModule,
    NBModuleBay,
    NBModuleType,
    NBInventoryItem,
    NBManufacturer,
)
from module.sources.check_redfish.import_inventory import CheckRedfish


def make_source(model_components_as_modules: bool, netbox_api_version: str):
    """
    Build a minimally initialized CheckRedfish source backed by a fresh (reset) inventory.
    The real add_necessary_base_objects() runs so custom fields are registered exactly as in
    production (against dcim.module or dcim.inventoryitem depending on the chosen backend).
    """

    inventory = NetBoxInventory()
    # reset the singleton state so each test starts from an empty inventory
    inventory.init()
    inventory.source_list = list()
    inventory.netbox_api_version = netbox_api_version

    source = object.__new__(CheckRedfish)
    source.inventory = inventory
    source.name = "test"
    source.source_tag = "Source: test"
    source.settings = types.SimpleNamespace(model_components_as_modules=model_components_as_modules)

    # registers the source tag and all custom fields via the real code path
    source.add_necessary_base_objects()

    device = inventory.add_object(NBDevice, data={"name": "server01"}, source=source)
    source.device_object = device

    return source, inventory, device


def cpu_item(full_name="Socket 1 (Intel Xeon Gold 6248R)",
             model="Intel Xeon Gold 6248R",
             serial="CPU-AAA",
             manufacturer="Intel",
             health="OK"):
    """Build a normalized CPU component item as produced by CheckRedfish.update_proc()."""
    return {
        "inventory_type": "CPU",
        "description": ["x86-64", "Cores: 24", "Threads: 48"],
        "manufacturer": manufacturer,
        "full_name": full_name,
        "model": model,
        "serial": serial,
        "health": health,
        "size": "24/48",
        "speed": "3.0GHz",
    }


@pytest.mark.parametrize("flag, api_version, expected", [
    (True, "4.3.0", True),
    (True, "4.3.1", True),
    (True, "5.0.0", True),
    (True, "4.2.9", False),   # NetBox too old -> fall back to inventory items
    (True, "4.0.0", False),
    (False, "4.3.0", False),  # feature disabled -> inventory items
    (False, "5.0.0", False),
])
def test_use_modules_decision_matrix(flag, api_version, expected):
    source, _, _ = make_source(flag, api_version)
    assert source.use_modules() is expected


def test_creates_full_module_graph_for_cpu():
    source, inventory, device = make_source(True, "4.3.0")

    source.update_all_items([cpu_item()])

    modules = inventory.get_all_items(NBModule)
    bays = inventory.get_all_items(NBModuleBay)
    module_types = inventory.get_all_items(NBModuleType)

    # exactly one of each object is created and no deprecated inventory item is touched
    assert len(modules) == 1
    assert len(bays) == 1
    assert len(module_types) == 1
    assert len(inventory.get_all_items(NBInventoryItem)) == 0

    module = modules[0]
    bay = bays[0]
    module_type = module_types[0]

    # the module is wired to the device, its bay and its module type (same object instances)
    assert module.data["device"] is device
    assert module.data["module_bay"] is bay
    assert module.data["module_type"] is module_type
    assert module.data["status"] == "active"
    assert module.data["serial"] == "CPU-AAA"

    # descriptive data lives in custom fields on the module
    assert grab(module, "data.custom_fields.inventory_type") == "CPU"
    assert grab(module, "data.custom_fields.inventory_size") == "24/48"
    assert grab(module, "data.custom_fields.inventory_speed") == "3.0GHz"
    assert grab(module, "data.custom_fields.health") == "OK"

    # the bay is the physical slot, scoped to the device
    assert bay.data["name"] == "Socket 1 (Intel Xeon Gold 6248R)"
    assert bay.data["device"] is device

    # the module type is the catalog entry carrying the real CPU model + manufacturer
    assert module_type.data["model"] == "Intel Xeon Gold 6248R"
    assert grab(module_type, "data.manufacturer.data.name") == "Intel"

    # the module derives its display name from the bay (it has no name of its own)
    assert module.get_display_name(including_second_key=True) == \
        "Socket 1 (Intel Xeon Gold 6248R) (server01)"


def test_module_sync_is_idempotent():
    source, inventory, _ = make_source(True, "4.3.0")

    source.update_all_items([cpu_item()])
    source.update_all_items([cpu_item()])

    # a second run with identical data must not create duplicates
    assert len(inventory.get_all_items(NBModule)) == 1
    assert len(inventory.get_all_items(NBModuleBay)) == 1
    assert len(inventory.get_all_items(NBModuleType)) == 1


def test_same_model_reuses_module_type_across_devices():
    source, inventory, _ = make_source(True, "4.3.0")

    # first device gets a CPU
    source.update_all_items([cpu_item(serial="CPU-AAA")])

    # a second device with the exact same CPU model
    device2 = inventory.add_object(NBDevice, data={"name": "server02"}, source=source)
    source.device_object = device2
    source.update_all_items([cpu_item(serial="CPU-BBB")])

    # the module type (catalog entry) is shared, but each device gets its own bay + module
    assert len(inventory.get_all_items(NBModuleType)) == 1
    assert len(inventory.get_all_items(NBModule)) == 2
    assert len(inventory.get_all_items(NBModuleBay)) == 2
    assert len(inventory.get_all_items(NBManufacturer)) == 1


def test_different_model_creates_distinct_module_type():
    """This is the 'one server type, different CPUs' use case."""
    source, inventory, _ = make_source(True, "4.3.0")

    source.update_all_items([cpu_item(model="Intel Xeon Gold 6248R", serial="CPU-AAA")])

    device2 = inventory.add_object(NBDevice, data={"name": "server02"}, source=source)
    source.device_object = device2
    source.update_all_items([
        cpu_item(full_name="Socket 1 (Intel Xeon Gold 5318Y)",
                 model="Intel Xeon Gold 5318Y", serial="CPU-BBB")
    ])

    models = sorted(grab(mt, "data.model") for mt in inventory.get_all_items(NBModuleType))
    assert models == ["Intel Xeon Gold 5318Y", "Intel Xeon Gold 6248R"]
    assert len(inventory.get_all_items(NBModule)) == 2


def test_missing_component_marks_module_health_absent():
    source, inventory, _ = make_source(True, "4.3.0")

    cpu1 = cpu_item(full_name="Socket 1 (Intel Xeon Gold 6248R)", serial="CPU-AAA")
    cpu2 = cpu_item(full_name="Socket 2 (Intel Xeon Gold 6248R)", serial="CPU-BBB")
    source.update_all_items([cpu1, cpu2])

    assert len(inventory.get_all_items(NBModule)) == 2

    # second CPU disappears from the inventory file
    source.update_all_items([cpu1])

    modules_by_bay = {
        grab(m, "data.module_bay.data.name"): m for m in inventory.get_all_items(NBModule)
    }
    assert grab(modules_by_bay["Socket 1 (Intel Xeon Gold 6248R)"],
                "data.custom_fields.health") == "OK"
    assert grab(modules_by_bay["Socket 2 (Intel Xeon Gold 6248R)"],
                "data.custom_fields.health") == "Absent"


def test_inventory_item_backend_when_feature_disabled():
    source, inventory, device = make_source(False, "4.3.0")

    source.update_all_items([cpu_item()])

    # flag off -> the deprecated inventory item path is used, no modules created
    assert len(inventory.get_all_items(NBInventoryItem)) == 1
    assert len(inventory.get_all_items(NBModule)) == 0
    assert len(inventory.get_all_items(NBModuleBay)) == 0

    item = inventory.get_all_items(NBInventoryItem)[0]
    assert item.data["device"] is device
    assert grab(item, "data.custom_fields.inventory_type") == "CPU"


def test_inventory_item_backend_on_old_netbox_even_with_flag():
    source, inventory, _ = make_source(True, "4.2.9")

    source.update_all_items([cpu_item()])

    # NetBox too old for modules -> inventory items regardless of the flag
    assert len(inventory.get_all_items(NBInventoryItem)) == 1
    assert len(inventory.get_all_items(NBModule)) == 0
