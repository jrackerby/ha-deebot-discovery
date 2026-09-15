# TOOLS

What this integration's instruments — the robot, `deebot-client`, and the
entities they produce — do, refuse to do, and lie about. Per-repo by ruling
(jrackerby/HA GH-676): a trap about the robot lives here, never in the estate
file. Every line is a claim with a timestamp — re-verify before planning on
it, and edit it when it stops being true.

## The robot

- **A DEEBOT ON `mop_after_vacuum` DOCKS MID-JOB EVERY `mop_wash_interval` MINUTES.**
  Measured on Plumbus over a 2h job: ~15-19min cleaning, ~3m19s docked, resume,
  six times over, with battery only dipping to 75-83% and recovering ~10%. It is
  washing the mop - not finishing, not recharging. **A `docked` reading is NEVER
  evidence a job ended**, and `vacuum.returned_to_dock` fires on every wash. The job
  is over when the robot stops RESUMING, and `cleaned_area`/`cleaning_time`
  accumulate across the docks and reset to 0 only on a NEW job - which is how to
  tell a resumed job from a fresh one.
