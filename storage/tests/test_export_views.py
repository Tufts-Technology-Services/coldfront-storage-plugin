import csv
import io

import pytest
from coldfront.core.resource.models import Resource, ResourceType
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse

from storage.views import StorageAllocationExportView

EXPORT_URL = "/storage/allocation/export/"


def read_csv(response):
    """Drain a streaming CSV response into a list of rows."""
    content = b"".join(response.streaming_content).decode()
    return list(csv.reader(io.StringIO(content)))


def column(rows, name):
    """Return the values of one column, keyed by allocation id."""
    header, data = rows[0], rows[1:]
    index = header.index(name)
    return {row[0]: row[index] for row in data}


@pytest.fixture
def export(client, superuser):
    def _export():
        client.force_login(superuser)
        return read_csv(client.get(EXPORT_URL))

    return _export


def test_url_is_routed():
    assert reverse("storage-allocation-export") == EXPORT_URL


def test_anonymous_is_redirected_to_login(client, db):
    response = client.get(EXPORT_URL)
    assert response.status_code == 302
    assert EXPORT_URL in response["Location"]


def test_non_superuser_is_denied(regular_user):
    request = RequestFactory().get(EXPORT_URL)
    request.user = regular_user
    with pytest.raises(PermissionDenied):
        StorageAllocationExportView.as_view()(request)


def test_anonymous_request_is_not_served(db):
    request = RequestFactory().get(EXPORT_URL)
    request.user = AnonymousUser()
    assert StorageAllocationExportView.as_view()(request).status_code == 302


def test_response_is_a_csv_attachment(client, superuser, storage_resource, make_allocation):
    make_allocation(storage_resource)
    client.force_login(superuser)
    response = client.get(EXPORT_URL)

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert response["Content-Disposition"].startswith('attachment; filename="storage_allocations_')


def test_only_storage_allocations_are_exported(export, storage_resource, make_allocation):
    storage = make_allocation(storage_resource)
    cluster = Resource.objects.create(
        resource_type=ResourceType.objects.get_or_create(name="Cluster", description="compute")[0],
        name="cluster",
        description="a compute resource",
    )
    make_allocation(cluster)

    rows = export()
    assert [row[0] for row in rows[1:]] == [str(storage.pk)]


def test_allocation_on_several_storage_resources_appears_once(export, storage_resource, make_allocation):
    second = Resource.objects.create(
        resource_type=storage_resource.resource_type,
        name="other-storage",
        description="a second storage resource",
    )
    allocation = make_allocation(storage_resource, second)

    rows = export()
    assert [row[0] for row in rows[1:]] == [str(allocation.pk)]
    assert column(rows, "resources")[str(allocation.pk)] == "hpc-storage; other-storage"


def test_attribute_columns_are_discovered_and_filled(export, storage_resource, make_allocation):
    with_quota = make_allocation(storage_resource, attributes={"Storage Quota (TB)": "10"})
    without_quota = make_allocation(storage_resource)

    rows = export()
    quota = column(rows, "allocation_attribute: Storage Quota (TB)")
    assert quota[str(with_quota.pk)] == "10"
    assert quota[str(without_quota.pk)] == ""


def test_private_attributes_are_included(export, storage_resource, make_allocation):
    allocation = make_allocation(storage_resource, attributes={"secret_attr": "hidden"})

    rows = export()
    assert column(rows, "allocation_attribute: secret_attr")[str(allocation.pk)] == "hidden"


def test_repeated_attribute_values_share_a_cell(export, storage_resource, make_allocation):
    allocation = make_allocation(storage_resource, attributes={"Storage Path": "/one"})
    allocation.allocationattribute_set.create(
        allocation_attribute_type=allocation.allocationattribute_set.first().allocation_attribute_type,
        value="/two",
    )

    rows = export()
    assert column(rows, "allocation_attribute: Storage Path")[str(allocation.pk)] == "/one; /two"


def test_project_attributes_are_exported(export, storage_resource, make_allocation, make_project_attribute):
    allocation = make_allocation(storage_resource)
    make_project_attribute("Department", "Biology")

    rows = export()
    assert column(rows, "project_attribute: Department")[str(allocation.pk)] == "Biology"


def test_special_characters_round_trip(export, storage_resource, make_allocation, project):
    """Commas, quotes and newlines in free text must survive CSV quoting."""
    allocation = make_allocation(storage_resource, justification="line one\nline two, with a comma")

    rows = export()
    assert len(rows) == 2, "an embedded newline must not split the row"
    assert column(rows, "justification")[str(allocation.pk)] == "line one\nline two, with a comma"
    assert column(rows, "project_title")[str(allocation.pk)] == project.title


def test_every_row_has_the_full_width(export, storage_resource, make_allocation, make_project_attribute):
    make_allocation(storage_resource, attributes={"Storage Quota (TB)": "10"})
    make_allocation(storage_resource)
    make_project_attribute("Department", "Biology")

    rows = export()
    assert all(len(row) == len(rows[0]) for row in rows[1:])


def test_export_does_not_scale_queries_with_rows(
    django_assert_max_num_queries, client, superuser, storage_resource, make_allocation
):
    """Prefetching must keep the query count flat as allocations are added."""
    for _ in range(25):
        make_allocation(storage_resource, attributes={"Storage Quota (TB)": "10", "Storage Path": "/p"})

    client.force_login(superuser)
    with django_assert_max_num_queries(15):
        rows = read_csv(client.get(EXPORT_URL))

    assert len(rows) == 26
