from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import Account


@require_GET
def chart_of_accounts(request):
    accounts = Account.objects.filter(is_active=True).values("code", "name", "account_type")
    return JsonResponse({"accounts": list(accounts)})
