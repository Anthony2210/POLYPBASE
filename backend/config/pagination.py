from rest_framework.pagination import LimitOffsetPagination


class CappedLimitOffsetPagination(LimitOffsetPagination):
    # The box collection client requests 100 rows per page.
    max_limit = 100
