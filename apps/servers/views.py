from django.shortcuts import render, get_object_or_404
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import Server


@ensure_csrf_cookie
def servers_dashboard(request):
    servers = Server.objects.all().order_by('name')
    return render(request, "servers/dashboard.html", {"servers": servers})


def server_detail_page(request, server_id):
    server = get_object_or_404(Server, id=server_id)
    all_servers = Server.objects.all().order_by('name')
    return render(request, "servers/detail.html", {"server": server, "all_servers": all_servers})


def server_detail_fragment(request, server_id):
    server = get_object_or_404(Server, id=server_id)
    return render(request, "servers/_detail_fragment.html", {"server": server})
