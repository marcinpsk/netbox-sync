"""
Tests that paginated NetBox list requests return every row exactly once.

NetBox paginates with limit/offset. Several models order by a non-unique key
(dcim.modulebay is ordered by (device, name), and duplicate bay names on one
device are possible because the uniqueness constraint covers (device, module,
name) and top-level bays have a NULL module). Rows tied on the sort key have no
stable position between two queries, so a plain limit/offset walk can return one
tied row twice and never return another.

A row that is never returned is absent from the local inventory, so the sync
believes the object does not exist and creates a second one. That is how 116
duplicate module bays accumulated, which in turn made the modules in them flip
between orphaned and not on every run.

The real request() pagination loop is exercised against a real HTTP server that
reproduces the tie instability, so the test covers the actual wiring rather than
a description of it.
"""

import json
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from module.netbox.connection import NetBoxHandler
from module.netbox.object_classes import NBModuleBay

# 12 rows over 5-row pages, every row tied on the same sort key, so the server is
# free to order them differently on each page request - exactly what Postgres does.
TOTAL_ROWS = 12
PAGE_SIZE = 5


class _UnstableNetBoxHandler(BaseHTTPRequestHandler):
    """Serves a list endpoint whose row order is unstable for tied sort keys."""

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        limit = int(query.get("limit", [PAGE_SIZE])[0])
        offset = int(query.get("offset", ["0"])[0])
        ordering = query.get("ordering", [None])[0]

        self.server.requested_orderings.append(ordering)

        rows = list(self.server.rows)
        if ordering == "id":
            # a unique tiebreak makes the order total, so paging is stable
            rows.sort(key=lambda r: r["id"])
        else:
            # tied rows: rotate them, standing in for the arbitrary order a
            # database may return when the ORDER BY does not disambiguate
            self.server.rotation += 1
            shift = self.server.rotation % len(rows)
            rows = rows[shift:] + rows[:shift]

        page = rows[offset:offset + limit]
        next_url = None
        if offset + limit < len(rows):
            host, port = self.server.server_address[0], self.server.server_port
            next_url = f"http://{host}:{port}{parsed.path}?limit={limit}&offset={offset + limit}"
            if ordering is not None:
                next_url += f"&ordering={ordering}"

        body = json.dumps({"count": len(rows), "next": next_url, "results": page}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture
def netbox_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UnstableNetBoxHandler)
    server.rows = [{"id": i, "name": "DIMM A4", "device": {"id": 7, "name": "pve-3"}}
                   for i in range(1, TOTAL_ROWS + 1)]
    server.rotation = 0
    server.requested_orderings = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def handler(netbox_api):
    """A NetBoxHandler wired to the test server, with the real request() logic intact."""
    nb = object.__new__(NetBoxHandler)
    nb.url = f"http://127.0.0.1:{netbox_api.server_port}/api/"
    nb.session = requests.Session()
    nb.settings = types.SimpleNamespace(
        default_netbox_result_limit=PAGE_SIZE,
        max_retry_attempts=3,
        timeout=10,
        validate_tls_certs=False,
    )
    return nb


def test_every_row_is_returned_exactly_once(handler):
    """The regression: tied rows must not be dropped while walking the pages."""
    result = handler.request(NBModuleBay)

    returned = [row["id"] for row in result["results"]]

    assert sorted(returned) == list(range(1, TOTAL_ROWS + 1)), (
        f"paginated walk lost or duplicated rows: got {sorted(returned)}")


def test_list_requests_ask_for_a_unique_ordering(handler, netbox_api):
    """Without a unique tiebreak the server is free to reorder tied rows."""
    handler.request(NBModuleBay)

    assert netbox_api.requested_orderings, "no GET was issued"
    assert netbox_api.requested_orderings[0] == "id"


def test_ordering_is_kept_across_every_page(handler, netbox_api):
    """A tiebreak on page one only is still unstable on the pages after it."""
    handler.request(NBModuleBay)

    assert len(netbox_api.requested_orderings) > 1, "expected a paginated response"
    assert set(netbox_api.requested_orderings) == {"id"}


def test_caller_supplied_ordering_is_not_overridden(handler, netbox_api):
    """A source asking for a specific order keeps it."""
    handler.request(NBModuleBay, params={"ordering": "name"})

    assert netbox_api.requested_orderings[0] == "name"
