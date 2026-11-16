"""UI для управления службами."""
from django.shortcuts import render
from apps.accounts.access import section_required


@section_required("services")
def services_page(request):
    """Главная страница панели управления службами."""
    return render(request, "core/services_page.html")
