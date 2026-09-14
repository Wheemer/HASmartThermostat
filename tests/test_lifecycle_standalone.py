"""Unloading cancels active work and permanently closes the old instance."""

import ast
import asyncio
import importlib.util
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/smart_thermostat"
spec = importlib.util.spec_from_file_location("thermostat_lifecycle", COMPONENT / "lifecycle.py")
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


class Device:
    def __init__(self):
        self._operations = lifecycle.EntityOperations()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    @lifecycle.entity_operation
    async def send(self):
        self.calls.append("command")

    @lifecycle.entity_operation
    async def waiting_control(self):
        self.started.set()
        await self.release.wait()
        await self.send()

    @lifecycle.entity_operation
    async def nested(self):
        await self.send()
        await self.waiting_control()


class LifecycleTests(IsolatedAsyncioTestCase):
    async def test_unload_cancels_inflight_control_before_later_command(self):
        device = Device()
        task = asyncio.create_task(device.waiting_control())
        await device.started.wait()
        await device._operations.close()
        device.release.set()
        self.assertTrue(task.cancelled())
        self.assertFalse(device._operations.tasks)
        self.assertEqual(device.calls, [])

    async def test_late_callbacks_and_services_cannot_actuate(self):
        device = Device()
        await device._operations.close()
        await device.send()
        await device.waiting_control()
        self.assertEqual(device.calls, [])
        self.assertFalse(device.started.is_set())

    async def test_nested_calls_do_not_drop_tracking_of_outer_task(self):
        device = Device()
        task = asyncio.create_task(device.nested())
        await device.started.wait()
        self.assertEqual(device._operations.tasks, {task})
        await device._operations.close()
        self.assertTrue(task.cancelled())
        self.assertEqual(device.calls, ["command"])

    async def test_new_instance_operates_old_instance_stays_closed(self):
        old, new = Device(), Device()
        await old._operations.close()
        await new.send()
        await old.send()
        self.assertEqual(new.calls, ["command"])
        self.assertEqual(old.calls, [])

    async def test_repeated_unload_is_idempotent(self):
        device = Device()
        await device._operations.close()
        await device._operations.close()
        self.assertTrue(device._operations.closed)


class LifecycleCoverageTests(TestCase):
    def test_every_async_control_and_service_entrypoint_is_guarded(self):
        tree = ast.parse((COMPONENT / "climate.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SmartThermostat")
        for method in cls.body:
            if not isinstance(method, ast.AsyncFunctionDef):
                continue
            if method.name in ("async_added_to_hass", "async_will_remove_from_hass"):
                continue
            with self.subTest(method=method.name):
                self.assertIn("entity_operation", [ast.unparse(d) for d in method.decorator_list])
