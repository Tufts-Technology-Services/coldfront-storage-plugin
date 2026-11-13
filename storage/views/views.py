from coldfront.core.allocation.views import AllocationAttributeEditView as ColdfrontAllocationAttributeEditView

from ..constants import QUOTA_ATTRIBUTE_NAME
from ..forms import AllocationAttributeEditForm


class AllocationAttributeEditView(ColdfrontAllocationAttributeEditView):
    formset_class = AllocationAttributeEditForm

    def get_allocation_attributes_to_change(self, allocation_obj):
        attributes_to_change = allocation_obj.allocationattribute_set.select_related("allocation_attribute_type").all()

        attributes_to_change = [
            {
                "attribute_pk": attribute.pk,
                "name": attribute.allocation_attribute_type.name,
                "orig_value": attribute.value,
                "value": attribute.value,
                "is_private": attribute.allocation_attribute_type.is_private,
                # the quota changes only through a change request, which ticketing follows to done
                "read_only": attribute.allocation_attribute_type.name == QUOTA_ATTRIBUTE_NAME,
            }
            for attribute in attributes_to_change
        ]

        return attributes_to_change
