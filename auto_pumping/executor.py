from threading import Event, Thread
from time import sleep
from logging import getLogger, DEBUG


logger = getLogger(__name__)
logger.setLevel(DEBUG)


class ConfirmationRequest:
    """A pending request for the operator to approve a single plan step.

    Created by the executor thread and resolved from the UI event loop, so the
    handshake goes through a :class:`threading.Event`.
    """

    def __init__(self, step):
        self.step = step
        self._event = Event()
        self._approved = False

    @property
    def resolved(self):
        return self._event.is_set()

    def resolve(self, approved):
        self._approved = bool(approved)
        self._event.set()

    def wait_for_response(self, should_continue=None):
        """Block until the request is resolved, returning whether it was approved.

        Polls rather than blocking indefinitely so that a plan cancelled while
        the dialog is open does not leave this thread stuck forever.
        """
        while not self._event.wait(timeout=0.1):
            if should_continue is not None and not should_continue():
                return False
        return self._approved


class PlanExecutor:
    def __init__(self, pumping_system):
        self._pumping_system = pumping_system
        self._plan = None
        self._thread = None
        self._pending_confirmation = None
        self._confirmed_step = None

    @property
    def plan(self):
        return list(self._plan) if self._plan is not None else []

    @property
    def _current_step(self):
        return self._plan[0] if self._plan else None

    @property
    def pending_confirmation(self):
        return self._pending_confirmation

    def execute_plan(self, plan):
        self.cancel()
        self._plan = plan
        self._thread = Thread(target=self._plan_executor)
        self._thread.start()

    def cancel(self):
        self._plan = None
        self._confirmed_step = None
        # Release a thread parked on a confirmation dialog before joining it,
        # otherwise cancelling while the dialog is open deadlocks.
        pending = self._pending_confirmation
        if pending is not None:
            pending.resolve(False)
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self._pending_confirmation = None

    def _requires_confirmation(self, step):
        confirm = self._pumping_system._config.confirm
        if step.wait is not None:
            return False
        if step.valve is not None:
            return self._confirm_flag(
                confirm.valve, step.valve, step.state, confirm.default
            )
        if step.pump is not None:
            return self._confirm_flag(
                confirm.pump, step.pump, step.state, confirm.default
            )
        if step.stage is not None:
            # `confirm.stage` lists the positions that need confirmation; any
            # other position falls back to `confirm.default`.
            if confirm.stage is not None and step.stage in confirm.stage:
                return True
            return confirm.default
        return False

    @staticmethod
    def _confirm_flag(mapping, key, state, default):
        """Resolve a `Confirm` entry for a single target.

        A boolean entry applies to every action on that target, a state entry
        only applies when the action moves the target to that state, and an
        absent entry falls back to `confirm.default`.
        """
        if mapping is None or key not in mapping:
            return default
        value = mapping[key]
        if isinstance(value, bool):
            return value
        return value == state

    def _request_confirmation(self, step):
        """Ask the operator to approve `step`, blocking until they answer.

        A step may be revisited by the loop below while it waits for a safety
        condition, so the answer is remembered against that step rather than
        being asked again on every pass.
        """
        if self._confirmed_step is step:
            logger.debug(f"Step {step} already confirmed, skipping confirmation")
            return True
        if not self._requires_confirmation(step):
            logger.debug(f"Step {step} does not require confirmation, skipping confirmation")
            self._confirmed_step = step
            return True
        logger.info(f"Requesting confirmation for step {step}")
        request = ConfirmationRequest(step)
        self._pending_confirmation = request
        try:
            logger.debug(f"Waiting for operator confirmation for step {step}")
            approved = request.wait_for_response(lambda: self._plan is not None)
        finally:
            self._pending_confirmation = None
        if approved:
            logger.debug(f"Operator approved step {step}")
            self._confirmed_step = step
        return approved

    def _wait_satisfied(self, step):
        return all(
            ineq.evaluate(self._pumping_system.get_gauge_pressure(gauge))
            for gauge, ineq in step.wait.items()
        )

    def _complete_current_step(self, step):
        if self._plan and self._plan[0] is step:
            self._plan.pop(0)
        self._confirmed_step = None

    def _plan_executor(self):
        while self._plan is not None and len(self._plan) > 0:
            step = self._current_step
            logger.debug(f"Current step is {step}")
            if step.wait is None:
                logger.debug("Attempting replan")
                plan = None
                try:
                    plan = self._pumping_system._pumping_graph.make_plan(
                        self._pumping_system._get_state(),
                        **self._pumping_system._pumping_graph._last_goal_state_data,
                    )
                except Exception as e:
                    logger.error(f"Error while attempting to replan: {e}", exc_info=True)
                if plan is not None:
                    while plan and plan[0].wait is not None and self._wait_satisfied(plan[0]):
                        logger.debug(f"Skipping satisfied wait condition {plan[0].wait} while replanning")
                        plan.pop(0)
                    if not plan:
                        logger.info("Replanned goal is already satisfied")
                        self._plan = None
                        self._confirmed_step = None
                        return
                    if plan[0] != step:
                        logger.info(f"Plan changed")
                        self._plan = plan
                        self._confirmed_step = None
                        continue
            if step.wait is not None:
                logger.debug(f"Checking if wait condition {step.wait} is satisfied")
                if self._wait_satisfied(step):
                    logger.info(f"Wait condition {step.wait} satisfied")
                    self._complete_current_step(step)
                    continue
            if step.stage is not None:
                logger.debug(f"Checking if stage move to {step.stage} is allowed")
                if self._pumping_system._safety_machine.can_move_stage(step.stage):
                    logger.info(f"Stage move to {step.stage} allowed")
                    if self._request_confirmation(step):
                        logger.info(f"Moving stage to {step.stage}")
                        self._pumping_system._move_stage(step.stage)
                        self._complete_current_step(step)
                        continue
                    else:
                        logger.error(f"Stage move to {step.stage} not confirmed by operator. Cancelling plan.")
                        self._plan = None
                        self._confirmed_step = None
                        return
            elif step.valve is not None:
                logger.debug(f"Checking if valve {step.valve} can be actuated to {step.state}")
                if self._pumping_system._safety_machine.can_actuate_valve(step.valve, step.state):
                    logger.info(f"Valve {step.valve} can be actuated to {step.state}")
                    if self._request_confirmation(step):
                        logger.info(f"Actuating valve {step.valve} to {step.state}")
                        self._pumping_system._actuate_valve(step.valve, step.state)
                        self._complete_current_step(step)
                        continue
                    else:
                        logger.error(f"Valve actuation for {step.valve} to {step.state} not confirmed by operator. Cancelling plan.")
                        self._plan = None
                        self._confirmed_step = None
                        return
            elif step.pump is not None:
                logger.debug(f"Checking if pump {step.pump} can be turned {step.state}")
                if self._pumping_system._safety_machine.can_set_pump(step.pump, step.state):
                    logger.info(f"Pump {step.pump} can be set to {step.state}")
                    if self._request_confirmation(step):
                        logger.info(f"Setting pump {step.pump} to {step.state}")
                        self._pumping_system._set_pump(step.pump, step.state)
                        self._complete_current_step(step)
                        continue
                    else:
                        logger.error(f"Pump setting for {step.pump} to {step.state} not confirmed by operator. Cancelling plan.")
                        self._plan = None
                        self._confirmed_step = None
                        return
            sleep(1)
