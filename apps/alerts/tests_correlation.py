"""
Корреляция алертов (apps/alerts/correlation.py): подавление каскадов по зависимостям.

Топология в тестах:
    core ─┬─ dist ─┬─ acc1 ── NE (шлюз acc1)
          │        └─ acc2
          │        └─ подсеть 10.9.0.0/24 (uplink = dist)
    other (сам по себе)
"""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.access import EDIT
from apps.accounts.models import User
from apps.alerts import correlation as corr
from apps.alerts.models import Alert
from apps.devices.models import Device, NeDevice
from apps.subnets.models import IpAddress, Subnet

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "corr-tests"}}


@override_settings(CACHES=LOCMEM)
class CorrelationTests(TestCase):
    def setUp(self):
        cache.clear()
        p = mock.patch("apps.accounts.telegram_notify._dispatch", return_value=0)
        self.dispatch = p.start()
        self.addCleanup(p.stop)
        self.core = Device.objects.create(name="core", ip="10.0.0.1")
        self.dist = Device.objects.create(name="dist", ip="10.0.0.2", uplink=self.core)
        self.acc1 = Device.objects.create(name="acc1", ip="10.0.0.3", uplink=self.dist)
        self.acc2 = Device.objects.create(name="acc2", ip="10.0.0.4", uplink=self.dist)
        self.other = Device.objects.create(name="other", ip="10.0.0.9")
        self.ne = NeDevice.objects.create(gateway=self.acc1, ne_id=7)
        self.net = Subnet.objects.create(cidr="10.9.0.0/24", uplink=self.dist)
        self.ip = IpAddress.objects.create(subnet=self.net, ip="10.9.0.10", is_monitored=True)

    # ── как это создают реальные задачи: create() → notify_alert_created() ──
    def fire(self, device=None, key="device_unreachable", sev="crit", **kw):
        from apps.accounts.telegram_notify import notify_alert_created
        a = Alert.objects.create(device=device, metric_key=key, severity=sev, message=f"{key} {device}", **kw)
        notify_alert_created(a)
        a.refresh_from_db()
        return a

    def resolve(self, a, when=None):
        from apps.accounts.telegram_notify import notify_alert_resolved
        a.state, a.resolved_at = Alert.STATE_OK, when or timezone.now()
        a.save()
        notify_alert_resolved(a)

    def sent_tags(self):
        return [c.args[2] for c in self.dispatch.call_args_list]

    def test_graph_helpers(self):
        umap = corr.uplink_map()
        self.assertEqual(corr.chain_up(self.acc1.pk, umap), [self.acc1.pk, self.dist.pk, self.core.pk])
        self.assertEqual(corr.descendants(self.core.pk, umap), {self.dist.pk, self.acc1.pk, self.acc2.pk})
        self.assertTrue(corr.would_cycle(self.core.pk, self.acc1.pk))
        self.assertFalse(corr.would_cycle(self.other.pk, self.acc1.pk))
        order = [d.name for d in corr.topo_order(Device.objects.all())]
        self.assertLess(order.index("core"), order.index("dist"))
        self.assertLess(order.index("dist"), order.index("acc1"))
        self.assertEqual(corr.dependents(self.dist.pk), {"devices": 2, "ne": 1, "links": 0, "subnets": 1})

    def test_children_after_root_are_silent(self):
        root = self.fire(self.core)
        self.assertIsNone(root.suppressed_by_id)
        text = self.dispatch.call_args.args[0]
        self.assertIn("За ним: 3 устр., 1 NE, 1 подсет.", text)
        children = [self.fire(self.dist), self.fire(self.acc1, "if_oper_status:5", "warn"),
                    self.fire(self.acc2, "cpu", "warn"), self.fire(None, "ne_status", ne_device=self.ne),
                    self.fire(None, f"ip_ping:{self.ip.pk}", ip_address=self.ip)]
        for c in children:
            self.assertEqual(c.suppressed_by_id, root.pk, c.metric_key)
        self.assertEqual(self.sent_tags(), [f"alert_created:{root.pk}"])
        # закрытие следствия тоже молча, закрытие причины — с уведомлением
        self.resolve(children[1])
        self.resolve(root)
        self.assertEqual(self.sent_tags(), [f"alert_created:{root.pk}", f"alert_resolved:{root.pk}"])

    def test_unrelated_device_not_affected(self):
        self.fire(self.core)
        a = self.fire(self.other)
        self.assertIsNone(a.suppressed_by_id)
        self.assertEqual(len(self.sent_tags()), 2)

    def test_own_metrics_folded_under_own_unreachable(self):
        root = self.fire(self.acc2)
        port = self.fire(self.acc2, "if_oper_status:1", "warn")
        self.assertEqual(port.suppressed_by_id, root.pk)

    def test_topmost_cause_wins(self):
        dist_root = self.fire(self.dist)
        acc = self.fire(self.acc1)
        self.assertEqual(acc.suppressed_by_id, dist_root.pk)
        core_root = self.fire(self.core)
        dist_root.refresh_from_db()
        acc.refresh_from_db()
        self.assertEqual(dist_root.suppressed_by_id, core_root.pk)
        self.assertEqual(acc.suppressed_by_id, core_root.pk)

    def test_early_child_held_then_adopted(self):
        Device.objects.filter(pk=self.core.pk).update(ping_fail_count=2)   # ядро уже не отвечает
        ne_alert = self.fire(None, "ne_status", ne_device=self.ne)
        self.assertIsNotNone(ne_alert.notify_held_at)
        self.assertEqual(self.sent_tags(), [])
        Device.objects.filter(pk=self.core.pk).update(ping_fail_count=4)
        root = self.fire(self.core)
        ne_alert.refresh_from_db()
        self.assertEqual((ne_alert.suppressed_by_id, ne_alert.notify_held_at), (root.pk, None))
        self.assertEqual(self.sent_tags(), [f"alert_created:{root.pk}"])

    def test_held_released_when_cause_never_comes(self):
        Device.objects.filter(pk=self.dist.pk).update(ping_fail_count=1)
        a = self.fire(self.acc2, "cpu", "warn")
        self.assertIsNotNone(a.notify_held_at)
        corr.tick()                                          # ещё рано
        self.assertEqual(self.sent_tags(), [])
        Device.objects.filter(pk=self.dist.pk).update(ping_fail_count=0)   # dist ответил — причины нет
        corr.tick()
        a.refresh_from_db()
        self.assertIsNone(a.notify_held_at)
        self.assertEqual(self.sent_tags(), [f"alert_created:{a.pk}"])

    def test_hold_is_capped(self):
        Device.objects.filter(pk=self.dist.pk).update(ping_fail_count=1)
        a = self.fire(self.acc2, "cpu", "warn")
        corr.tick(now=timezone.now() + timedelta(seconds=corr.HOLD_MAX_SEC + 1))
        self.assertEqual(self.sent_tags(), [f"alert_created:{a.pk}"])

    def test_orphan_reported_after_cause_resolves(self):
        root = self.fire(self.core)
        child = self.fire(self.acc1)
        self.resolve(root, when=timezone.now() - timedelta(seconds=corr.ORPHAN_GRACE_SEC + 5))
        corr.tick()
        child.refresh_from_db()
        self.assertIsNone(child.suppressed_by_id)
        self.assertIn(f"alert_orphan:{child.pk}", self.sent_tags())
        # закрытие бывшего следствия — уже с уведомлением
        self.resolve(child)
        self.assertIn(f"alert_resolved:{child.pk}", self.sent_tags())

    def test_orphan_moves_to_remaining_cause(self):
        dist_root = self.fire(self.dist)
        core_root = self.fire(self.core)
        child = self.fire(self.acc1)
        self.assertEqual(child.suppressed_by_id, core_root.pk)
        self.resolve(core_root, when=timezone.now() - timedelta(minutes=10))
        corr.tick()
        child.refresh_from_db()
        self.assertEqual(child.suppressed_by_id, dist_root.pk)   # dist всё ещё лежит — причина он

    def test_escalation_skips_suppressed(self):
        from apps.alerts.ack import escalate_unacked
        root = self.fire(self.core)
        self.fire(self.acc1)
        Alert.objects.update(started_at=timezone.now() - timedelta(hours=1))
        self.dispatch.reset_mock()
        self.assertEqual(escalate_unacked(), 1)
        self.assertEqual(self.sent_tags(), [f"alert_escalation:{root.pk}"])

    def test_pages(self):
        root = self.fire(self.core)
        self.fire(self.acc1)
        admin = User.objects.create_superuser("root", password="x-12345")
        self.client.force_login(admin)
        r = self.client.get("/alerts/")
        self.assertContains(r, "Свёрнуто следствий: 1")
        self.assertEqual(r.context["counts"]["firing_total"], 1)
        r = self.client.get("/admin-ui/dependencies/")
        self.assertContains(r, "acc1")
        r = self.client.get(f"/admin-ui/devices/{self.dist.pk}/")
        self.assertContains(r, "за ним")
        # бот: в списке только причина, в карточке — свёрнутые
        from apps.accounts.access import Access
        from apps.accounts.bot import core
        ctx = core.Ctx(user=admin, acc=Access.for_user(admin), tg_id=1)
        self.assertIn("+1 свёрнуто", core.run("al.l", ctx, ["all", "0"]).text)
        self.assertIn("Свёрнуто следствий: 1", core.run("al.c", ctx, [str(root.pk)]).text)

    def test_dependencies_page_saves_and_blocks_cycles(self):
        user = User.objects.create_user("eng", password="x-12345", is_active=True)
        user.access_sections = {"devices": EDIT}
        user.save()
        self.client.force_login(user)
        base = {f"dev_{d.pk}": f"d{d.uplink_id}" if d.uplink_id else "" for d in Device.objects.all()}
        base.update({f"sub_{self.net.pk}": self.dist.pk})
        bad = dict(base, **{f"dev_{self.core.pk}": f"d{self.acc1.pk}"})     # core → acc1 → dist → core
        self.client.post("/admin-ui/dependencies/", bad)
        self.core.refresh_from_db()
        self.assertIsNone(self.core.uplink_id)
        good = dict(base, **{f"dev_{self.other.pk}": f"d{self.core.pk}", f"sub_{self.net.pk}": ""})
        r = self.client.post("/admin-ui/dependencies/", good)
        self.assertEqual(r.status_code, 302)
        self.other.refresh_from_db()
        self.net.refresh_from_db()
        self.assertEqual((self.other.uplink_id, self.net.uplink_id), (self.core.pk, None))

    def test_device_form_rejects_cycle(self):
        from apps.adminui.forms import DeviceForm
        form = DeviceForm(instance=self.core)
        self.assertNotIn(self.acc1, form.fields["uplink"].queryset)
        self.assertIn(self.other, form.fields["uplink"].queryset)


@override_settings(CACHES=LOCMEM)
class PingCascadeTests(TestCase):
    """Настоящая задача пинга: узел и всё за ним падают разом → одно уведомление."""

    def test_one_notification_for_whole_branch(self):
        from types import SimpleNamespace

        from apps.monitoring.tasks import DEVICE_PING_FAILS, ping_all_devices
        cache.clear()
        # имена подобраны так, что по алфавиту/pk дети идут раньше узла — порядок решает topo_order
        acc = Device.objects.create(name="a-acc", ip="10.1.0.3")
        dist = Device.objects.create(name="b-dist", ip="10.1.0.2")
        core = Device.objects.create(name="c-core", ip="10.1.0.1")
        Device.objects.filter(pk=acc.pk).update(uplink=dist)
        Device.objects.filter(pk=dist.pk).update(uplink=core)
        alive = Device.objects.create(name="d-ok", ip="10.1.0.9")
        snmp = mock.MagicMock()
        snmp.return_value.get_one = mock.AsyncMock(return_value=None)
        with mock.patch("apps.accounts.telegram_notify._dispatch", return_value=0) as dispatch, \
                mock.patch("apps.subnets.scanner.ping_one",
                           side_effect=lambda ip, **kw: SimpleNamespace(alive=ip == alive.ip)), \
                mock.patch("apps.monitoring.snmp.SnmpClient", snmp):
            for _ in range(DEVICE_PING_FAILS):
                ping_all_devices()
                cache.clear()          # замок singleton между прогонами
        roots = Alert.objects.filter(metric_key="device_unreachable", state="firing")
        self.assertEqual(roots.count(), 3)
        top = roots.get(device=core)
        self.assertEqual(set(roots.exclude(pk=top.pk).values_list("suppressed_by", flat=True)), {top.pk})
        self.assertEqual([c.args[2] for c in dispatch.call_args_list], [f"alert_created:{top.pk}"])


@override_settings(CACHES=LOCMEM)
class OpticalChainTests(TestCase):
    """
    Цепочка NE по оптике от центра (ЛАЗ):
        mux (шлюз) ─ NE1@ЛАЗ ─L1─ NE2@S1 ─L2─ NE3@S2 ─L3─ NE4@S3
        opt@ЛАЗ ─L4─ acc@S4          (коммутатор по оптике, без ручного uplink)
    L2 заведена «задом наперёд» (A = дальний конец) — направление должно определиться от центра.
    """

    def setUp(self):
        from apps.stations.models import OpticalLink, Station
        cache.clear()
        p = mock.patch("apps.accounts.telegram_notify._dispatch", return_value=0)
        self.dispatch = p.start()
        self.addCleanup(p.stop)
        st = {n: Station.objects.create(slug=n.lower(), name=n) for n in ("LAZ", "S1", "S2", "S3", "S4")}
        self.mux = Device.objects.create(name="mux", ip="10.2.0.1")
        self.opt = Device.objects.create(name="opt", ip="10.2.0.2")
        self.acc = Device.objects.create(name="acc", ip="10.2.0.3")
        self.ne = {i: NeDevice.objects.create(gateway=self.mux, ne_id=i, name=f"NE{i}") for i in (1, 2, 3, 4)}

        def link(sa, a, sb, b):
            kw = {"station_a": st[sa], "station_b": st[sb]}
            kw["ne_a" if isinstance(a, NeDevice) else "device_a"] = a
            kw["ne_b" if isinstance(b, NeDevice) else "device_b"] = b
            return OpticalLink.objects.create(**kw)
        self.l1 = link("LAZ", self.ne[1], "S1", self.ne[2])
        self.l2 = link("S2", self.ne[3], "S1", self.ne[2])
        self.l3 = link("S2", self.ne[3], "S3", self.ne[4])
        self.l4 = link("LAZ", self.opt, "S4", self.acc)
        self.st = st

    def fire(self, key, sev="crit", **kw):
        from apps.accounts.telegram_notify import notify_alert_created
        a = Alert.objects.create(metric_key=key, severity=sev, message=key, **kw)
        notify_alert_created(a)
        a.refresh_from_db()
        return a

    def link_alert(self, link, sev="crit"):
        # как _upsert_link_alert: ne_device = конец A, device = его шлюз
        return self.fire(f"optical_link:{link.pk}", sev, ne_device=link.ne_a,
                         device=link.ne_a.gateway if link.ne_a else link.device_a)

    def test_graph_from_links(self):
        g = corr.build_graph()
        n = {i: f"n{self.ne[i].pk}" for i in self.ne}
        self.assertEqual(g.hub_names, ["LAZ"])                      # авто: самая связанная станция
        self.assertEqual(g.parent[n[1]], f"d{self.mux.pk}")         # головной — за шлюзом
        self.assertEqual(g.parent[n[2]], f"l{self.l1.pk}")
        self.assertEqual(g.parent[f"l{self.l1.pk}"], n[1])
        self.assertEqual(g.parent[n[3]], f"l{self.l2.pk}")          # L2 развёрнута от центра
        self.assertEqual(g.parent[f"l{self.l2.pk}"], n[2])
        self.assertEqual(g.parent[n[4]], f"l{self.l3.pk}")
        self.assertEqual(g.parent[f"d{self.acc.pk}"], f"l{self.l4.pk}")
        # ручной uplink важнее линии
        Device.objects.filter(pk=self.acc.pk).update(uplink=self.mux)
        self.assertEqual(corr.build_graph().parent[f"d{self.acc.pk}"], f"d{self.mux.pk}")

    def test_explicit_hub(self):
        self.st["S2"].is_hub = True
        self.st["S2"].save()
        g = corr.build_graph()
        self.assertEqual(g.hub_names, ["S2"])
        self.assertEqual(g.parent[f"l{self.l2.pk}"], f"n{self.ne[3].pk}")   # теперь от S2 к S1

    def test_link_break_folds_everything_beyond(self):
        cause = self.link_alert(self.l2)                                   # обрыв S1–S2
        self.assertIn("За ним: 2 NE, 1 лин.", self.dispatch.call_args.args[0])
        beyond = [self.link_alert(self.l3, "power"),                        # «нет связи» дальше
                  self.fire("sfp_rx_dbm:1", "warn", ne_device=self.ne[4], device=self.mux),
                  self.fire("ne_status", "crit", ne_device=self.ne[3], device=self.mux)]
        for a in beyond:
            self.assertEqual(a.suppressed_by_id, cause.pk, a.metric_key)
        before = self.fire("sfp_temp:2", "warn", ne_device=self.ne[2], device=self.mux)
        self.assertIsNone(before.suppressed_by_id)                          # NE до обрыва — сам по себе
        self.assertEqual([c.args[2] for c in self.dispatch.call_args_list],
                         [f"alert_created:{cause.pk}", f"alert_created:{before.pk}"])

    def test_upper_break_takes_over(self):
        lower = self.link_alert(self.l2)
        tail = self.fire("ne_status", "crit", ne_device=self.ne[4], device=self.mux)
        upper = self.link_alert(self.l1)
        lower.refresh_from_db()
        tail.refresh_from_db()
        self.assertEqual((lower.suppressed_by_id, tail.suppressed_by_id), (upper.pk, upper.pk))

    def test_rx_warning_is_not_a_cause(self):
        warn = self.link_alert(self.l2, "warn")
        tail = self.fire("ne_status", "crit", ne_device=self.ne[4], device=self.mux)
        self.assertIsNone(warn.suppressed_by_id)
        self.assertIsNone(tail.suppressed_by_id)

    def test_gateway_down_folds_all_ne(self):
        root = self.fire("device_unreachable", device=self.mux)
        a = self.link_alert(self.l3, "power")
        b = self.fire("sfp_rx_dbm:1", "warn", ne_device=self.ne[1], device=self.mux)
        self.assertEqual((a.suppressed_by_id, b.suppressed_by_id), (root.pk, root.pk))

    def test_far_alert_held_while_link_is_down(self):
        from apps.stations.models import OpticalLink
        OpticalLink.objects.filter(pk=self.l2.pk).update(link_status="crit")   # статус уже есть, алерта ещё нет
        early = self.fire("sfp_rx_dbm:1", "warn", ne_device=self.ne[4], device=self.mux)
        self.assertIsNotNone(early.notify_held_at)
        cause = self.link_alert(self.l2)
        early.refresh_from_db()
        self.assertEqual((early.suppressed_by_id, early.notify_held_at), (cause.pk, None))
        self.assertEqual([c.args[2] for c in self.dispatch.call_args_list], [f"alert_created:{cause.pk}"])

    def test_poll_order_upstream_first(self):
        from apps.stations.models import OpticalLink
        order = [l.pk for l in corr.topo_order(OpticalLink.objects.order_by("-pk"), prefix="l")]
        self.assertLess(order.index(self.l1.pk), order.index(self.l2.pk))
        self.assertLess(order.index(self.l2.pk), order.index(self.l3.pk))

    def test_dependencies_page_shows_chain(self):
        admin = User.objects.create_superuser("root", password="x-12345")
        self.client.force_login(admin)
        r = self.client.get("/admin-ui/dependencies/")
        self.assertContains(r, "NE-4")
        self.assertContains(r, "mux › NE1 › NE2 › NE3")
        self.assertContains(r, "головной")
        r = self.client.post("/admin-ui/dependencies/", {"hub": self.st["S2"].pk})
        self.st["S2"].refresh_from_db()
        self.assertTrue(self.st["S2"].is_hub)


    # ── Коммутатор, подключённый через LAN-порт NE ──
    def test_switch_behind_ne_folds_on_link_break(self):
        sw = Device.objects.create(name="sw-s3", ip="10.2.0.30", uplink_ne=self.ne[4])
        g = corr.build_graph()
        self.assertEqual(g.parent[f"d{sw.pk}"], f"n{self.ne[4].pk}")
        self.assertIn(f"d{sw.pk}", g.below(f"l{self.l2.pk}"))
        cause = self.link_alert(self.l2)
        self.assertIn("1 устр.", self.dispatch.call_args.args[0])
        down = self.fire("device_unreachable", device=sw)
        port = self.fire("if_oper_status:3", "warn", device=sw)
        self.assertEqual((down.suppressed_by_id, port.suppressed_by_id), (cause.pk, cause.pk))

    def test_cycle_through_ne_chain_rejected(self):
        Device.objects.filter(pk=self.mux.pk).update(uplink=self.opt)
        # opt за NE3, а NE3 через линии → NE1 → mux → opt: цикл
        self.assertEqual(corr.cycles_with({self.opt.pk: f"n{self.ne[3].pk}"}), sorted([self.opt.pk, self.mux.pk]))
        admin = User.objects.create_superuser("root", password="x-12345")
        self.client.force_login(admin)
        self.client.post("/admin-ui/dependencies/", {f"dev_{self.opt.pk}": f"n{self.ne[3].pk}"})
        self.opt.refresh_from_db()
        self.assertIsNone(self.opt.uplink_ne_id)
        from apps.adminui.forms import DeviceForm
        form = DeviceForm(instance=self.opt)
        self.assertNotIn(self.ne[3], form.fields["uplink_ne"].queryset)    # NE ниже по графу не предлагаются

    def test_save_ne_parent_and_station_hint(self):
        from apps.stations.models import StationDevice
        sw = Device.objects.create(name="sw-s2", ip="10.2.0.20")
        StationDevice.objects.create(station=self.st["S2"], device=sw)
        StationDevice.objects.create(station=self.st["LAZ"], device=self.opt)   # центр — без подсказки
        self.assertEqual(corr.ne_suggestions(), {sw.pk: self.ne[3].pk})
        admin = User.objects.create_superuser("root", password="x-12345")
        self.client.force_login(admin)
        r = self.client.get("/admin-ui/dependencies/")
        self.assertContains(r, "За этот NE")
        self.client.post("/admin-ui/dependencies/", {f"dev_{sw.pk}": f"n{self.ne[3].pk}"})
        sw.refresh_from_db()
        self.assertEqual((sw.uplink_id, sw.uplink_ne_id), (None, self.ne[3].pk))
        self.assertEqual(corr.ne_suggestions(), {})                                # уже в цепочке
        r = self.client.get(f"/admin-ui/devices/{sw.pk}/")
        self.assertContains(r, "NE3 › <b>sw-s2</b>")
