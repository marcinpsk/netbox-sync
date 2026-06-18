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
    NBDeviceType,
    NBInterface,
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


def cpu_item(bay_name="Socket 1",
             model="Intel Xeon Gold 6248R",
             serial="CPU-AAA",
             manufacturer="Intel",
             health="OK",
             full_name=None):
    """Build a normalized CPU component item as produced by CheckRedfish.update_proc().

    bay_name is the stable physical slot (the module bay identity); full_name is the
    display name and by default embeds the model, exactly like the real parser does.
    """
    return {
        "inventory_type": "CPU",
        "description": ["x86-64", "Cores: 24", "Threads: 48"],
        "manufacturer": manufacturer,
        "bay_name": bay_name,
        "full_name": full_name if full_name is not None else f"{bay_name} ({model})",
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

    # the bay is the stable physical slot (model lives in the module type, not the bay name)
    assert bay.data["name"] == "Socket 1"
    assert bay.data["device"] is device

    # the module type is the catalog entry carrying the real CPU model + manufacturer
    assert module_type.data["model"] == "Intel Xeon Gold 6248R"
    assert grab(module_type, "data.manufacturer.data.name") == "Intel"

    # the module derives its display name from the bay (it has no name of its own)
    assert module.get_display_name(including_second_key=True) == "Socket 1 (server01)"


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
    source.update_all_items([cpu_item(model="Intel Xeon Gold 5318Y", serial="CPU-BBB")])

    models = sorted(grab(mt, "data.model") for mt in inventory.get_all_items(NBModuleType))
    assert models == ["Intel Xeon Gold 5318Y", "Intel Xeon Gold 6248R"]
    assert len(inventory.get_all_items(NBModule)) == 2


def test_same_bay_new_model_updates_module_type():
    """Same device + same physical bay + a replaced CPU model across runs.

    Regression for CodeRabbit PR #1: the bay must be keyed on a stable slot (not the
    model-bearing display name), so a model swap reuses the same bay and the module
    re-points to the new module type instead of churning the bay or keeping a stale type.
    """
    source, inventory, _ = make_source(True, "4.3.0")

    # first run: CPU model A installed in socket "Socket 1"
    source.update_all_items([cpu_item(bay_name="Socket 1",
                                      model="Intel Xeon Gold 6248R", serial="CPU-AAA")])

    # second run: same device, same socket, a different CPU model is now installed
    source.update_all_items([cpu_item(bay_name="Socket 1",
                                      model="Intel Xeon Gold 5318Y", serial="CPU-BBB")])

    bays = inventory.get_all_items(NBModuleBay)
    modules = inventory.get_all_items(NBModule)

    # the physical bay is stable: still a single bay holding a single module on the device
    assert len(bays) == 1
    assert len(modules) == 1
    assert bays[0].data["name"] == "Socket 1"

    module = modules[0]
    # the module re-points to the replaced part's module type (no stale catalog reference)
    assert grab(module, "data.module_type.data.model") == "Intel Xeon Gold 5318Y"
    assert module.data["serial"] == "CPU-BBB"


def test_missing_component_marks_module_health_absent():
    source, inventory, _ = make_source(True, "4.3.0")

    cpu1 = cpu_item(bay_name="Socket 1", serial="CPU-AAA")
    cpu2 = cpu_item(bay_name="Socket 2", serial="CPU-BBB")
    source.update_all_items([cpu1, cpu2])

    assert len(inventory.get_all_items(NBModule)) == 2

    # second CPU disappears from the inventory file
    source.update_all_items([cpu1])

    modules_by_bay = {
        grab(m, "data.module_bay.data.name"): m for m in inventory.get_all_items(NBModule)
    }
    assert grab(modules_by_bay["Socket 1"], "data.custom_fields.health") == "OK"
    assert grab(modules_by_bay["Socket 2"], "data.custom_fields.health") == "Absent"


def fan_item(bay_name="System Board Fan1 (ID: 0.56)", health="OK"):
    """A component that reports no manufacturer (fans, enclosures, PCIe extenders, ...)."""
    return {
        "inventory_type": "Fan",
        "description": ["Context: SystemBoard"],
        "full_name": bay_name,
        "health": health,
        "speed": "9240RPM",
    }


def test_component_without_manufacturer_uses_device_manufacturer():
    """NetBox requires a manufacturer on a module type. Components that report none (fans,
    storage enclosures, PCIe extenders) must still get one, otherwise the module-type POST
    fails with 'manufacturer required' and the whole module create cascade fails."""
    source, inventory, device = make_source(True, "4.3.0")

    # give the device a manufacturer via its device type, like a real synced device has
    manufacturer = inventory.add_object(NBManufacturer, data={"name": "Acme"}, source=source)
    device_type = inventory.add_object(
        NBDeviceType, data={"model": "PowerEdge R650", "manufacturer": manufacturer}, source=source)
    device.update(data={"device_type": device_type}, source=source)

    source.update_all_items([fan_item()])

    module_types = inventory.get_all_items(NBModuleType)
    assert len(module_types) == 1
    assert len(inventory.get_all_items(NBModule)) == 1

    # the (required) manufacturer is populated from the device's vendor
    device_manufacturer = grab(device, "data.device_type.data.manufacturer.data.name")
    assert grab(module_types[0], "data.manufacturer.data.name") == device_manufacturer


def test_component_without_manufacturer_falls_back_to_unknown():
    """When neither the component nor the device exposes a manufacturer, fall back to a
    placeholder so the required module type field is always populated."""
    source, inventory, _ = make_source(True, "4.3.0")  # device has no device type / manufacturer

    source.update_all_items([fan_item()])

    module_types = inventory.get_all_items(NBModuleType)
    assert len(module_types) == 1
    assert grab(module_types[0], "data.manufacturer.data.name") == "Unknown"
    assert len(inventory.get_all_items(NBModule)) == 1


def test_existing_module_type_manufacturer_is_preserved():
    """If a module type for this model already exists in NetBox with a manufacturer (set by a
    previous sync or curated by hand), a later sync of a component that reports no manufacturer
    must reuse it, not overwrite it with the device-vendor / 'Unknown' fallback."""
    source, inventory, _ = make_source(True, "4.3.0")

    # a module type for this model already exists in NetBox, manufacturer "Globex"
    globex = inventory.add_object(NBManufacturer, data={"name": "Globex"}, source=source)
    inventory.add_object(
        NBModuleType, data={"model": "PCIe Extender", "manufacturer": globex}, source=source)

    # the PCIe extender reports no manufacturer
    source.update_all_items([{
        "inventory_type": "Storage Controller",
        "full_name": "PCIe Extender",
        "model": "PCIe Extender",
        "health": "OK",
        "description": ["LDs: 1, PDs: 1"],
    }])

    module_types = [mt for mt in inventory.get_all_items(NBModuleType)
                    if grab(mt, "data.model") == "PCIe Extender"]
    assert len(module_types) == 1
    # the pre-existing manufacturer is preserved, not clobbered by the fallback
    assert grab(module_types[0], "data.manufacturer.data.name") == "Globex"


def test_nic_and_bmc_interfaces_are_attached_to_their_modules():
    """NIC port interfaces are attached to their adapter's module and the BMC interface to the
    manager module, so NetBox cascade-deletes them when the module is removed (module FK)."""
    source, inventory, _ = make_source(True, "4.3.0")
    source.interface_adapter_type_dict = {}
    source.nic_module_bay_by_adapter_id = {}
    source.manager_name = None
    source.settings.overwrite_interface_name = False
    source.settings.overwrite_interface_attributes = False
    source.settings.permitted_subnets = None  # ports carry no IPs, so this is never dereferenced
    source.settings.ip_tenant_inheritance_order = []

    source.inventory_file_content = {
        "inventory": {
            "manager": [
                {"name": "iDRAC 9", "model": None, "licenses": [], "firmware": "7.0",
                 "health_status": "OK"}
            ],
            "network_adapter": [
                {"id": "NIC.Slot.1", "name": "NIC.Slot.1", "model": "BCM57414",
                 "manufacturer": "Broadcom", "operation_status": "Enabled", "num_ports": "2",
                 "serial": "NIC-AAA", "firmware": "1.0"}
            ],
            "network_port": [
                {"id": "NIC.Slot.1-1", "name": "Slot 1 Port 1", "adapter_id": "NIC.Slot.1",
                 "operation_status": "Enabled", "link_status": "Up", "addresses": [],
                 "capable_speed": 10000, "manager_ids": []},
                {"id": "NIC.1", "name": "iDRAC", "adapter_id": None,
                 "operation_status": "Enabled", "link_status": "Up", "addresses": [],
                 "capable_speed": 1000, "manager_ids": ["iDRAC.Embedded.1"]},
            ],
        }
    }

    source.update_manager()
    source.update_network_adapter()
    source.update_network_interface()

    interfaces = {grab(i, "data.name"): i for i in inventory.get_all_items(NBInterface)}
    nic_interface = interfaces["NIC.Slot.1-1"]
    bmc_interface = interfaces["iDRAC 9 (NIC.1)"]

    # the NIC port belongs to its adapter's module, the BMC port to the manager module
    assert grab(nic_interface, "data.module.data.module_bay.data.name") == "NIC.Slot.1"
    assert grab(bmc_interface, "data.module.data.module_bay.data.name") == "iDRAC 9"

    # and it really is the same module object created for this device
    assert grab(nic_interface, "data.module") is source.find_device_module_by_bay_name("NIC.Slot.1")


def test_nic_port_interface_named_by_stable_redfish_id():
    """With modules on, a NIC port is named by its stable redfish id (e.g. NIC.Slot.1-1) rather
    than the long human label prepended to it; the descriptive label moves to the description."""
    source, inventory, _ = make_source(True, "4.3.0")
    source.interface_adapter_type_dict = {}
    source.nic_module_bay_by_adapter_id = {}
    source.manager_name = None
    source.settings.overwrite_interface_name = False
    source.settings.overwrite_interface_attributes = False
    source.settings.permitted_subnets = None
    source.settings.ip_tenant_inheritance_order = []

    source.inventory_file_content = {
        "inventory": {
            "network_adapter": [
                {"id": "NIC.Integrated.1", "name": "NIC.Integrated.1", "model": "BCM57412",
                 "manufacturer": "Broadcom", "operation_status": "Enabled", "num_ports": "1"}
            ],
            "network_port": [
                {"id": "NIC.Integrated.1-1", "name": "Integrated NIC 1 Port 1 Partition 1",
                 "adapter_id": "NIC.Integrated.1", "operation_status": "Enabled",
                 "link_status": "Up", "addresses": [], "capable_speed": 10000,
                 "manager_ids": []},
            ],
        }
    }

    source.update_network_adapter()
    source.update_network_interface()

    interfaces = {grab(i, "data.name"): i for i in inventory.get_all_items(NBInterface)}

    # the stable id is the name; the description carries the human label, not the name
    assert "NIC.Integrated.1-1" in interfaces
    assert "Integrated NIC 1 Port 1 Partition 1 (NIC.Integrated.1-1)" not in interfaces
    assert grab(interfaces["NIC.Integrated.1-1"], "data.description") == \
        "Integrated NIC 1 Port 1 Partition 1"


def test_interfaces_not_attached_to_modules_when_feature_disabled():
    """With the modules feature off, interfaces must not get a module reference."""
    source, inventory, _ = make_source(False, "4.3.0")
    source.interface_adapter_type_dict = {}
    source.nic_module_bay_by_adapter_id = {}
    source.manager_name = None
    source.settings.overwrite_interface_name = False
    source.settings.overwrite_interface_attributes = False
    source.settings.permitted_subnets = None
    source.settings.ip_tenant_inheritance_order = []

    source.inventory_file_content = {
        "inventory": {
            "network_adapter": [
                {"id": "NIC.Slot.1", "name": "NIC.Slot.1", "model": "BCM57414",
                 "manufacturer": "Broadcom", "operation_status": "Enabled", "num_ports": "2"}
            ],
            "network_port": [
                {"id": "NIC.Slot.1-1", "name": "Slot 1 Port 1", "adapter_id": "NIC.Slot.1",
                 "operation_status": "Enabled", "link_status": "Up", "addresses": [],
                 "capable_speed": 10000, "manager_ids": []},
            ],
        }
    }

    source.update_network_adapter()
    source.update_network_interface()

    interfaces = inventory.get_all_items(NBInterface)
    assert len(interfaces) == 1
    assert grab(interfaces[0], "data.module") is None
    # with the feature off, the legacy "<label> (<id>)" interface name is preserved unchanged
    assert grab(interfaces[0], "data.name") == "Slot 1 Port 1 (NIC.Slot.1-1)"
    assert len(inventory.get_all_items(NBModule)) == 0


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
