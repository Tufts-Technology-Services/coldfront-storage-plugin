import csv
from collections import defaultdict

from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.project.models import ProjectAttribute
from coldfront.core.utils.common import Echo
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.views.generic import View

from storage.constants import STORAGE_RESOURCE_TYPE_NAME

# attributes are not necessarily unique per allocation/project, so repeated values share a cell
MULTI_VALUE_SEPARATOR = '; '

ALLOCATION_ATTRIBUTE_COLUMN_PREFIX = 'allocation_attribute: '
PROJECT_ATTRIBUTE_COLUMN_PREFIX = 'project_attribute: '

BASE_COLUMNS = [
    'allocation_id',
    'allocation_status',
    'resources',
    'quantity',
    'start_date',
    'end_date',
    'is_locked',
    'is_changeable',
    'description',
    'justification',
    'created',
    'modified',
    'project_id',
    'project_title',
    'project_code',
    'project_status',
    'project_institution',
    'project_field_of_science',
    'project_pi_username',
    'project_pi_name',
    'project_pi_email',
]


def format_datetime(value):
    """Render a datetime in local time, tolerating naive values when USE_TZ is off."""
    if value is None:
        return ''
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.isoformat(timespec='seconds')


def base_values(allocation):
    """Return the non-attribute values for an allocation, ordered to match BASE_COLUMNS."""
    project = allocation.project
    pi = project.pi
    return [
        allocation.pk,
        allocation.status.name,
        MULTI_VALUE_SEPARATOR.join(sorted(resource.name for resource in allocation.resources.all())),
        allocation.quantity,
        allocation.start_date,
        allocation.end_date,
        allocation.is_locked,
        allocation.is_changeable,
        allocation.description or '',
        allocation.justification,
        format_datetime(allocation.created),
        format_datetime(allocation.modified),
        project.pk,
        project.title,
        project.project_code,
        project.status.name,
        project.institution,
        project.field_of_science.description,
        pi.username,
        pi.get_full_name(),
        pi.email,
    ]


def attribute_values(attributes, type_name):
    """Map attribute type name to its value(s) for one allocation or project.

    Params:
        attributes (iterable): the attribute objects to collapse
        type_name (callable): returns the attribute type name for an attribute
    """
    values = defaultdict(list)
    for attribute in attributes:
        values[type_name(attribute)].append(attribute.value)
    return {name: MULTI_VALUE_SEPARATOR.join(value) for name, value in values.items()}


class StorageAllocationExportView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Streams every storage allocation, with its allocation and project attributes, as a CSV."""

    def test_func(self):
        return self.request.user.is_superuser

    def get_queryset(self):
        return (
            Allocation.objects.filter(resources__resource_type__name=STORAGE_RESOURCE_TYPE_NAME)
            .select_related(
                'status',
                'project',
                'project__pi',
                'project__status',
                'project__field_of_science',
            )
            .prefetch_related(
                'resources',
                'allocationattribute_set__allocation_attribute_type',
                'project__projectattribute_set__proj_attr_type',
            )
            .order_by('pk')
            .distinct()
        )

    def get_allocation_attribute_names(self):
        """Return every allocation attribute type name used by a storage allocation."""
        return sorted(
            AllocationAttribute.objects.filter(
                allocation__resources__resource_type__name=STORAGE_RESOURCE_TYPE_NAME,
            )
            .values_list('allocation_attribute_type__name', flat=True)
            .distinct()
        )

    def get_project_attribute_names(self):
        """Return every project attribute type name used by a project holding a storage allocation."""
        return sorted(
            ProjectAttribute.objects.filter(
                project__allocation__resources__resource_type__name=STORAGE_RESOURCE_TYPE_NAME,
            )
            .values_list('proj_attr_type__name', flat=True)
            .distinct()
        )

    def rows(self, allocation_attribute_names, project_attribute_names):
        writer = csv.writer(Echo())

        header = (
            BASE_COLUMNS
            + [ALLOCATION_ATTRIBUTE_COLUMN_PREFIX + name for name in allocation_attribute_names]
            + [PROJECT_ATTRIBUTE_COLUMN_PREFIX + name for name in project_attribute_names]
        )
        yield writer.writerow(header)

        for allocation in self.get_queryset().iterator(chunk_size=200):
            allocation_attributes = attribute_values(
                allocation.allocationattribute_set.all(),
                lambda attribute: attribute.allocation_attribute_type.name,
            )
            project_attributes = attribute_values(
                allocation.project.projectattribute_set.all(),
                lambda attribute: attribute.proj_attr_type.name,
            )
            yield writer.writerow(
                base_values(allocation)
                + [allocation_attributes.get(name, '') for name in allocation_attribute_names]
                + [project_attributes.get(name, '') for name in project_attribute_names]
            )

    def get(self, request, *args, **kwargs):
        filename = 'storage_allocations_{}.csv'.format(timezone.localtime().strftime('%Y%m%d-%H%M%S'))
        response = StreamingHttpResponse(
            self.rows(self.get_allocation_attribute_names(), self.get_project_attribute_names()),
            content_type='text/csv',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
