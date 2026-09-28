from threading import Thread
from time import sleep


class PlanExecutor:
    def __init__(self, pumping_system):
        self._pumping_system = pumping_system
        self._current_step = None
        self._plan = None
        self._thread = None

    @property
    def plan(self):
        return ([self._current_step] if self._current_step is not None else []) + (
            self._plan if self._plan is not None else []
        )

    def execute_plan(self, plan):
        self.cancel()
        self._plan = plan
        self._thread = Thread(target=self._plan_executor)
        self._thread.start()

    def cancel(self):
        self._plan = None
        self._current_step = None
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _plan_executor(self):
        while self._plan is not None and len(self._plan) > 0:
            if self._current_step is None:
                self._current_step = self._plan.pop(0)
            if self._current_step.wait is not None:
                for gauge, ineq in self._current_step.wait.gauge.items():
                    if ineq.evaluate(self._pumping_system.get_gauge_pressure(gauge)):
                        self._current_step = None
                        continue
            if (
                self._current_step.stage is not None
                and self._pumping_system._move_stage(self._current_step.stage)
            ):
                self._current_step = None
                continue
            if (
                self._current_step.valve is not None
                and self._pumping_system._actuate_valve(
                    self._current_step.valve, self._current_step.state
                )
            ):
                self._current_step = None
                continue
            if self._current_step.pump is not None and self._pumping_system._set_pump(
                self._current_step.pump, self._current_step.state
            ):
                self._current_step = None
                continue
            sleep(1)
