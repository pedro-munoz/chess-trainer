/* Port of chess_trainer/srs.py — SM-2-lite scheduling.

   In the static build this is the only scheduler: training state lives in the
   phone's IndexedDB, not in the server's `scheduling` table. Constants must
   stay in step with the Python. */

export const FAIL_RETRY_S = 10 * 60;
export const FIRST_INTERVALS_DAYS = [1, 3];
export const MIN_EASE = 1.3;
export const MAX_EASE = 3.2;
export const DAY_S = 86400;

export function blank() {
  return { ease: 2.5, interval_days: 0, due_at: 0, reps: 0, lapses: 0 };
}

/** Pure: previous scheduling state + outcome -> next state. */
export function review(prev, correct, now) {
  const s = { ...blank(), ...(prev || {}) };
  let { ease, interval_days: interval, reps, lapses } = s;
  let dueAt;

  if (correct) {
    reps += 1;
    ease = Math.min(MAX_EASE, ease + 0.05);
    interval = reps <= FIRST_INTERVALS_DAYS.length
      ? FIRST_INTERVALS_DAYS[reps - 1]
      : interval * ease;
    dueAt = now + Math.trunc(interval * DAY_S);
  } else {
    reps = 0;
    lapses += 1;
    ease = Math.max(MIN_EASE, ease - 0.2);
    interval = 0;
    dueAt = now + FAIL_RETRY_S;
  }

  return { ease, interval_days: interval, due_at: dueAt, reps, lapses };
}
