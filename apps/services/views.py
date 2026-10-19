"""UI для управления службами."""
from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required


@staff_member_required
def services_page(request):
    """Главная страница панели управления службами."""
    return render(request, "core/services_page.html")
