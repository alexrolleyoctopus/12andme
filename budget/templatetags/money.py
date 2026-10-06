from django import template
from decimal import Decimal

register = template.Library()


@register.filter
def money(value):
    return f"${Decimal(value or 0)/100:,.2f}"
