from django import forms

from coldfront.core.allocation.models import AllocationAttribute


class AllocationAttributeEditForm(forms.Form):
    attribute_pk = forms.IntegerField(required=False, disabled=True)
    name = forms.CharField(max_length=150, required=False, disabled=True)
    orig_value = forms.CharField(max_length=150, required=False, disabled=True)
    value = forms.CharField(max_length=150, required=False, disabled=False)
    is_private = forms.BooleanField(required=False, disabled=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["attribute_pk"].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        allocation_attribute = AllocationAttribute.objects.get(pk=cleaned_data.get("attribute_pk"))

        allocation_attribute.value = cleaned_data.get("value")
        allocation_attribute.clean()
