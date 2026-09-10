# check_redfish inventory fixtures

`dell_r740xd_storage_enclosure.json` is a captured `check_redfish` inventory, not a
hand-written one. It holds the `storage_enclosure` entry a Dell PowerEdge R740xd
reports through check_redfish 2.1.2, so tests run against the object shape the
collector really produces.

To take a new capture, run check_redfish against a host and keep the entry you need
from the inventory file it writes:

```bash
check_redfish --host <bmc> --username <user> --password <password> \
              --inventory <output.json> --all
```

Remove anything that identifies a host before committing a capture: service tags,
serial numbers, BMC and management IP addresses, host names, NetBox ids. In this file
the `physical_drive_ids` hash suffixes are replaced with same-shaped placeholders;
everything else is Dell product and Redfish nomenclature, which the tests need.

Note what this hardware does **not** report, because it shapes what a fixture can
cover: `manufacturer`, `num_bays`, `firmware` and `serial` are all null, and both the
storage controllers and the physical drives report `location: null`. A capture from
this machine therefore exercises `update_storage_enclosure()` only. Tests for the
controller and physical drive paths build their input in the test module and are
synthetic.
