from django.http import Http404, JsonResponse

from .cases import CASES


def case_view(request, version, case):
    if case not in CASES or version not in ("before", "after"):
        raise Http404
    before, after = CASES[case]
    fn = before if version == "before" else after
    return JsonResponse({"result": fn()}, safe=False)
