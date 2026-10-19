"""Прокси для внешних тайлов (OpenRailwayMap) — обход 403."""
import urllib.request
import urllib.error

from django.http import HttpResponse, Http404
from django.views.decorators.cache import cache_page


@cache_page(60 * 60)  # кеш 1 час
def orm_tile(request, style, z, x, y):
    """Прокси для tiles.openrailwaymap.org/{style}/{z}/{x}/{y}.png"""
    url = f"https://tiles.openrailwaymap.org/{style}/{z}/{x}/{y}.png"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Cerberus monitoring)",
        "Referer": "https://www.openrailwaymap.org/",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read()
        response = HttpResponse(content, content_type="image/png")
        response["Cache-Control"] = "public, max-age=3600"
        return response
    except urllib.error.HTTPError as e:
        raise Http404(f"Tile {e.code}")
    except Exception as e:
        raise Http404(f"Tile error: {e}")
