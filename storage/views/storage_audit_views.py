from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.db.models import Q, Prefetch, QuerySet
from django.views.generic import ListView
from django.shortcuts import get_object_or_404
from datetime import date
from coldfront.core.allocation.models import Allocation, AllocationAttribute

from storage.forms import StorageAuditSearchForm



class StorageAuditListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = Allocation
    template_name = "storage/storage_audit_list.html"
    context_object_name = "allocation_list"
    paginate_by = 25

    def test_func(self):
        """UserPassesTestMixin Tests"""
        pk = self.kwargs.get("pk")
        _ = get_object_or_404(Allocation, pk=pk)
        if self.request.user.has_perm("allocation.can_view_all_allocations"):
            return True
        return False
    
    def get_quota_attr_prefetch(self) -> Prefetch:
        """Return Prefetch for allocation quota attribute."""
        return Prefetch(
            'allocationattribute_set',
            queryset=AllocationAttribute.objects.filter(allocation_attribute_type__name=QUOTA_ATTR_NAME).only(
                'allocation_id', 'value'
            ),
            to_attr=QUOTA_ATTRIBUTE_NAME,
        )

    def get_queryset(self):
        order_by = self.request.GET.get("order_by")
        if order_by:
            direction = self.request.GET.get("direction")
            dir_dict = {"asc": "", "des": "-"}
            order_by = dir_dict[direction] + order_by
        else:
            order_by = "id"

        storage_audit_search_form = StorageAuditSearchForm(self.request.GET)

        if storage_audit_search_form.is_valid():
            data = storage_audit_search_form.cleaned_data

            allocations = (
                Allocation.objects.select_related(
                    "project",
                    "project__pi",
                    "status",
                )
                .prefetch_related(self.get_quota_attr_prefetch())

                .filter(resources__resource_type__name="Storage")
                .order_by(order_by)
            )


            # Project Title
            if data.get("project"):
                allocations = allocations.filter(project__title__icontains=data.get("project"))

            # username
            if data.get("username"):
                allocations = allocations.filter(
                    Q(project__pi__username__icontains=data.get("username"))
                    | Q(allocationuser__user__username__icontains=data.get("username"))
                    & Q(allocationuser__status__name__in=["PendingEULA", "Active"])
                )

            # Resource Name
            if data.get("resource_name"):
                allocations = allocations.filter(resources__in=data.get("resource_name"))

            # Allocation Attribute Name
            if data.get("allocation_attribute_name") and data.get("allocation_attribute_value"):
                allocations = allocations.filter(
                    Q(allocationattribute__allocation_attribute_type=data.get("allocation_attribute_name"))
                    & Q(allocationattribute__value=data.get("allocation_attribute_value"))
                )

            # End Date
            if data.get("end_date"):
                allocations = allocations.filter(end_date__lt=data.get("end_date"), status__name="Active").order_by(
                    "end_date"
                )

            # Active from now until date
            if data.get("active_from_now_until_date"):
                allocations = allocations.filter(end_date__gte=date.today())
                allocations = allocations.filter(
                    end_date__lt=data.get("active_from_now_until_date"), status__name="Active"
                ).order_by("end_date")

            # Status
            if data.get("status"):
                allocations = allocations.filter(status__in=data.get("status"))

        else:
            allocations = (
                Allocation.objects.select_related(
                    "project",
                    "project__pi",
                    "status",
                )
                .filter(
                    Q(allocationuser__user=self.request.user)
                    & Q(allocationuser__status__name__in=["PendingEULA", "Active"])
                )
                .order_by(order_by)
            )

        return allocations.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allocations_count = self.get_queryset().count()
        context["allocations_count"] = allocations_count

        storage_audit_search_form = StorageAuditSearchForm(self.request.GET)

        if storage_audit_search_form.is_valid():
            data = storage_audit_search_form.cleaned_data
            filter_parameters = ""
            for key, value in data.items():
                if value:
                    if isinstance(value, QuerySet):
                        filter_parameters += "".join([f"{key}={ele.pk}&" for ele in value])
                    elif hasattr(value, "pk"):
                        filter_parameters += f"{key}={value.pk}&"
                    else:
                        filter_parameters += f"{key}={value}&"
            context["storage_audit_search_form"] = storage_audit_search_form
        else:
            filter_parameters = None
            context["storage_audit_search_form"] = StorageAuditSearchForm()

        order_by = self.request.GET.get("order_by")
        if order_by:
            direction = self.request.GET.get("direction")
            filter_parameters_with_order_by = filter_parameters + "order_by=%s&direction=%s&" % (order_by, direction)
        else:
            filter_parameters_with_order_by = filter_parameters

        if filter_parameters:
            context["expand_accordion"] = "show"
        context["filter_parameters"] = filter_parameters
        context["filter_parameters_with_order_by"] = filter_parameters_with_order_by

        allocation_list = context.get("allocation_list")
        paginator = Paginator(allocation_list, self.paginate_by)

        page = self.request.GET.get("page")

        try:
            allocation_list = paginator.page(page)
        except PageNotAnInteger:
            allocation_list = paginator.page(1)
        except EmptyPage:
            allocation_list = paginator.page(paginator.num_pages)

        return context