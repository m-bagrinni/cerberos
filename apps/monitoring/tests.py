from django.test import SimpleTestCase


class TaskCodeTests(SimpleTestCase):
    def test_link_poll_does_not_shadow_module_helpers(self):
        """
        Локальный import _end_state/_calc_link_status внутри poll_optical_links_fast делал их локальными
        переменными всей функции: линия, обработанная до ветки с импортом, падала с UnboundLocalError
        (проявилось, когда линии стали опрашиваться от центра — apps/alerts/correlation.topo_order).
        """
        from apps.monitoring.tasks import poll_optical_links_fast
        fn = poll_optical_links_fast.run
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        local = set(fn.__code__.co_varnames)
        self.assertFalse(local & {"_end_state", "_calc_link_status"}, local & {"_end_state", "_calc_link_status"})
