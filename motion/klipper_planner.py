# Klipper motion planner timing port
from __future__ import annotations

import math

MIN_CRUISE_RATIO = 0.5


class PlannerMove:

    __slots__ = ("move_d", "ux", "uy", "accel", "junction_deviation",
                 "max_cruise_v2", "delta_v2", "smooth_delta_v2",
                 "max_start_v2", "max_smoothed_v2",
                 "start_v", "cruise_v", "end_v",
                 "accel_t", "cruise_t", "decel_t")

    def __init__(self, dist, ux, uy, speed, accel, junction_deviation,
                 accel_to_decel):
        self.move_d = dist
        self.ux = ux
        self.uy = uy
        self.accel = accel
        self.junction_deviation = junction_deviation
        self.max_cruise_v2 = speed * speed
        self.delta_v2 = 2.0 * dist * accel
        self.smooth_delta_v2 = 2.0 * dist * accel_to_decel
        self.max_start_v2 = 0.0
        self.max_smoothed_v2 = 0.0
        self.start_v = self.cruise_v = self.end_v = 0.0
        self.accel_t = self.cruise_t = self.decel_t = 0.0

    def calc_junction(self, prev: "PlannerMove") -> None:
        max_start_v2 = min(self.max_cruise_v2, prev.max_cruise_v2,
                           prev.max_start_v2 + prev.delta_v2)
        junction_cos_theta = -(self.ux * prev.ux + self.uy * prev.uy)
        sin_theta_d2 = math.sqrt(max(0.5 * (1.0 - junction_cos_theta), 0.0))
        cos_theta_d2 = math.sqrt(max(0.5 * (1.0 + junction_cos_theta), 0.0))
        one_minus = 1.0 - sin_theta_d2
        if one_minus > 0.0 and cos_theta_d2 > 0.0:
            r_jd = sin_theta_d2 / one_minus
            tan_theta_d2 = sin_theta_d2 / cos_theta_d2
            move_cent = 0.5 * self.move_d * tan_theta_d2 * self.accel
            prev_cent = 0.5 * prev.move_d * tan_theta_d2 * prev.accel
            max_start_v2 = min(max_start_v2,
                               r_jd * self.junction_deviation * self.accel,
                               r_jd * prev.junction_deviation * prev.accel,
                               move_cent, prev_cent)
        self.max_start_v2 = max_start_v2
        self.max_smoothed_v2 = min(
            max_start_v2, prev.max_smoothed_v2 + prev.smooth_delta_v2)

    def set_junction(self, start_v2, cruise_v2, end_v2) -> None:
        half_inv_accel = 0.5 / self.accel
        accel_d = (cruise_v2 - start_v2) * half_inv_accel
        decel_d = (cruise_v2 - end_v2) * half_inv_accel
        cruise_d = self.move_d - accel_d - decel_d
        self.start_v = start_v = math.sqrt(start_v2)
        self.cruise_v = cruise_v = math.sqrt(cruise_v2)
        self.end_v = end_v = math.sqrt(end_v2)
        self.accel_t = (accel_d / ((start_v + cruise_v) * 0.5)
                        if (start_v + cruise_v) > 0 else 0.0)
        self.cruise_t = cruise_d / cruise_v if cruise_v > 0 else 0.0
        self.decel_t = (decel_d / ((end_v + cruise_v) * 0.5)
                        if (end_v + cruise_v) > 0 else 0.0)

    @property
    def duration(self) -> float:
        return self.accel_t + self.cruise_t + self.decel_t


def flush_queue(queue: list[PlannerMove]) -> float:
    if not queue:
        return 0.0
    delayed: list[tuple[PlannerMove, float, float]] = []
    next_end_v2 = next_smoothed_v2 = peak_cruise_v2 = 0.0
    for i in range(len(queue) - 1, -1, -1):
        move = queue[i]
        reachable_start_v2 = next_end_v2 + move.delta_v2
        start_v2 = min(move.max_start_v2, reachable_start_v2)
        reachable_smoothed_v2 = next_smoothed_v2 + move.smooth_delta_v2
        smoothed_v2 = min(move.max_smoothed_v2, reachable_smoothed_v2)
        if smoothed_v2 < reachable_smoothed_v2:
            if (smoothed_v2 + move.smooth_delta_v2 > next_smoothed_v2
                    or delayed):
                peak_cruise_v2 = min(
                    move.max_cruise_v2,
                    (smoothed_v2 + reachable_smoothed_v2) * 0.5)
                if delayed:
                    mc_v2 = peak_cruise_v2
                    for m, ms_v2, me_v2 in reversed(delayed):
                        mc_v2 = min(mc_v2, ms_v2)
                        m.set_junction(min(ms_v2, mc_v2), mc_v2,
                                       min(me_v2, mc_v2))
                    del delayed[:]
            cruise_v2 = min((start_v2 + reachable_start_v2) * 0.5,
                            move.max_cruise_v2, peak_cruise_v2)
            move.set_junction(min(start_v2, cruise_v2), cruise_v2,
                              min(next_end_v2, cruise_v2))
        else:
            delayed.append((move, start_v2, next_end_v2))
        next_end_v2 = start_v2
        next_smoothed_v2 = smoothed_v2
    if delayed:
        mc_v2 = peak_cruise_v2
        for m, ms_v2, me_v2 in reversed(delayed):
            mc_v2 = min(mc_v2, ms_v2)
            m.set_junction(min(ms_v2, mc_v2), mc_v2, min(me_v2, mc_v2))
    return sum(m.duration for m in queue)


class Planner:

    def __init__(self, accel_mm_s2, square_corner_velocity, max_speed_mm_s,
                 min_cruise_ratio=MIN_CRUISE_RATIO):
        self.accel = max(accel_mm_s2, 1.0)
        self.scv = max(square_corner_velocity, 0.1)
        self.max_speed = max(max_speed_mm_s, 1.0)
        self.junction_deviation = (
            self.scv * self.scv * (math.sqrt(2.0) - 1.0) / self.accel)
        self.accel_to_decel = self.accel * (1.0 - min_cruise_ratio)

    def run(self, ops) -> tuple[list[float], list[float]]:
        times = [0.0] * len(ops)
        durs = [0.0] * len(ops)
        t = 0.0
        queue: list[PlannerMove] = []
        idxs: list[int] = []
        prev: PlannerMove | None = None

        def flush():
            nonlocal t, queue, idxs, prev
            if queue:
                flush_queue(queue)
                for m, k in zip(queue, idxs):
                    t += m.duration
                    times[k] = t
                    durs[k] = m.duration
            queue, idxs, prev = [], [], None

        for k, op in enumerate(ops):
            if op[0] == "dwell":
                flush()
                t += op[1]
                times[k] = t
                durs[k] = op[1]
            else:
                _, dist, ux, uy, speed = op
                m = PlannerMove(dist, ux, uy, speed, self.accel,
                                self.junction_deviation, self.accel_to_decel)
                if prev is not None:
                    m.calc_junction(prev)
                queue.append(m)
                idxs.append(k)
                prev = m
        flush()
        return times, durs
